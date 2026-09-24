"""Daily OHLCV bars stored as a Parquet dataset.

On-disk layout, under a root directory::

    _metadata.json          format version, calendar and session range
    _assets.parquet         per-asset first/last session and currency
    year=2021/data.parquet  the bars of each calendar year
    year=2022/data.parquet
    ...

Bars are stored in long format, one row per (sid, session), with columns
``sid`` (int64), ``day`` (date32), ``open``/``high``/``low``/``close``
(float64) and ``volume`` (int64). Missing prices are null and missing volume
is 0, matching zipline's convention that a price of 0 means "no data". The
year partitions use Hive-style names, so the dataset can also be read directly
with ``pyarrow.dataset``, pandas, DuckDB and similar tools.

The reader loads one year of one field at a time into a dense
(sessions x sids) array and keeps recently used blocks in a small LRU cache.
Both access patterns zipline uses, loading windows of bars for many assets
and looking up single values, are then numpy indexing on cached blocks, and
memory stays bounded by the cache size rather than the dataset size.
"""

import os
from collections import OrderedDict
from functools import cached_property

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from zipline.data._parquet import (
    FIELDS,
    PRICE_FIELDS,
    as_sids,
    assets_path,
    epoch_nanos,
    handle_invalid,
    metadata_path,
    read_metadata,
    write_metadata,
)
from zipline.data.bar_reader import NoDataAfterDate, NoDataBeforeDate, NoDataOnDate
from zipline.data.session_bars import CurrencyAwareSessionBarReader
from zipline.utils.calendar_utils import get_calendar
from zipline.utils.cli import maybe_show_progress
from zipline.utils.date_utils import to_session_label

FORMAT_NAME = "zipline.daily_bars.parquet"
FORMAT_VERSION = 1

BARS_SCHEMA = pa.schema(
    [("sid", pa.int64()), ("day", pa.date32())]
    + [(field, pa.float64()) for field in PRICE_FIELDS]
    + [("volume", pa.int64())]
)
ASSETS_SCHEMA = pa.schema(
    [
        ("sid", pa.int64()),
        ("start_session", pa.date32()),
        ("end_session", pa.date32()),
        ("currency", pa.string()),
    ]
)

# Rows buffered per year before they are written out as a row group.
DEFAULT_ROW_GROUP_SIZE = 100_000
# Number of (year, field) blocks the reader keeps in memory.
DEFAULT_BLOCK_CACHE_SIZE = 16

DEFAULT_CURRENCY = "USD"

NANOS_PER_DAY = 86_400 * 10**9


def _year_path(rootdir, year):
    return os.path.join(rootdir, f"year={year}", "data.parquet")


class ParquetDailyBarWriter:
    """Write daily OHLCV bars to a Parquet dataset.

    Parameters
    ----------
    rootdir : str
        The directory to write the dataset into. It must not already contain
        a dataset.
    calendar : ExchangeCalendar
        The calendar the sessions belong to.
    start_session, end_session : pd.Timestamp
        The first and last sessions of the dataset.
    row_group_size : int, optional
        The number of rows buffered per year before being written.

    See Also
    --------
    zipline.data.parquet_daily_bars.ParquetDailyBarReader
    """

    def __init__(
        self,
        rootdir,
        calendar,
        start_session,
        end_session,
        row_group_size=DEFAULT_ROW_GROUP_SIZE,
    ):
        start_session = to_session_label(start_session)
        end_session = to_session_label(end_session)
        if not calendar.is_session(start_session):
            raise ValueError(f"Start session {start_session} is invalid!")
        if not calendar.is_session(end_session):
            raise ValueError(f"End session {end_session} is invalid!")

        self._rootdir = rootdir
        self._calendar = calendar
        self._start_session = start_session
        self._end_session = end_session
        self._row_group_size = row_group_size
        self._sessions = sessions = calendar.sessions_in_range(
            start_session, end_session
        )
        self._session_nanos = epoch_nanos(sessions)
        self._session_days = (self._session_nanos // NANOS_PER_DAY).astype("int32")
        # Positions in ``sessions`` at which each calendar year starts.
        years = sessions.year.to_numpy()
        self._year_starts = {
            int(year): int(pos)
            for year, pos in zip(*np.unique(years, return_index=True))
        }
        self._year_ends = {
            year: (self._year_starts.get(year + 1, len(sessions))) - 1
            for year in self._year_starts
        }

    @property
    def exists(self):
        """Whether ``rootdir`` already holds a dataset."""
        return os.path.exists(metadata_path(self._rootdir))

    def write(
        self,
        data,
        assets=None,
        show_progress=False,
        invalid_data_behavior="warn",
        currency_codes=None,
    ):
        """Write bars for each asset.

        Parameters
        ----------
        data : iterable[tuple[int, pd.DataFrame]]
            Pairs of sid and that asset's bars, indexed by session, with
            ``open``, ``high``, ``low``, ``close`` and ``volume`` columns.
            Sessions missing between an asset's first and last session are
            stored as missing data.
        assets : set[int], optional
            The sids expected in ``data``; any other sid raises ValueError.
            Also used to size the progress bar.
        show_progress : bool, optional
            Whether to show a progress bar.
        invalid_data_behavior : {'warn', 'raise', 'ignore'}, optional
            What to do about negative or infinite values, which are stored as
            missing data.
        currency_codes : pd.Series, optional
            Map from sid to the asset's listing currency. Defaults to USD.
        """
        if self.exists:
            raise ValueError(f"{self._rootdir} already contains a dataset")
        os.makedirs(self._rootdir, exist_ok=True)

        expected = set(assets) if assets is not None else None
        # Per year: buffered (sid, first position, values) chunks and rows.
        buffers = {}
        buffered_rows = {}
        writers = {}
        lifetimes = []

        def flush(year):
            table = self._bars_table(buffers.pop(year))
            buffered_rows.pop(year)
            if year not in writers:
                path = _year_path(self._rootdir, year)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                writers[year] = pq.ParquetWriter(path, BARS_SCHEMA, compression="zstd")
            writers[year].write_table(table)

        ctx = maybe_show_progress(
            iter(data),
            show_progress=show_progress,
            label="Merging daily equity files:",
            length=len(expected) if expected is not None else None,
        )
        try:
            with ctx as it:
                for sid, frame in it:
                    if expected is not None and sid not in expected:
                        raise ValueError(f"unknown asset id {sid!r}")
                    prepared = self._prepare(sid, frame, invalid_data_behavior)
                    if prepared is None:
                        continue
                    first, values = prepared
                    last = first + len(values) - 1
                    lifetimes.append((sid, first, last))

                    # Split the asset's bars at year boundaries.
                    for year in range(
                        int(self._sessions[first].year),
                        int(self._sessions[last].year) + 1,
                    ):
                        lo = max(first, self._year_starts[year])
                        hi = min(last, self._year_ends[year])
                        buffers.setdefault(year, []).append(
                            (sid, lo, values[lo - first : hi - first + 1])
                        )
                        rows = buffered_rows.get(year, 0) + hi - lo + 1
                        buffered_rows[year] = rows
                        if rows >= self._row_group_size:
                            flush(year)
            for year in list(buffers):
                flush(year)
        finally:
            for writer in writers.values():
                writer.close()

        first_trading_day = (
            self._sessions[min(first for _, first, _ in lifetimes)]
            if lifetimes
            else None
        )
        self._write_assets(lifetimes, currency_codes)
        # The metadata is written last, so its presence marks a complete
        # dataset.
        self._write_metadata(first_trading_day)

    def _prepare(self, sid, frame, invalid_data_behavior):
        """Validate one asset's bars and conform them to its sessions.

        Returns the position in ``sessions`` of the asset's first bar and a
        (sessions, OHLCV) float64 array covering every session through its
        last bar, or None if there are no bars.
        """
        missing = set(FIELDS) - set(frame.columns)
        if missing:
            raise ValueError(f"sid {sid}: missing columns {sorted(missing)}")
        if frame.empty:
            return None

        index = pd.DatetimeIndex(frame.index)
        if index.tz is not None:
            index = index.tz_convert("UTC").tz_localize(None)
        nanos = epoch_nanos(index.normalize())
        raw = frame[list(FIELDS)].to_numpy(dtype="float64")
        if not (np.diff(nanos) > 0).all():
            # Sort by date, keeping the last of any duplicated dates.
            order = np.argsort(nanos, kind="stable")
            nanos, raw = nanos[order], raw[order]
            keep = np.append(nanos[1:] != nanos[:-1], True)
            nanos, raw = nanos[keep], raw[keep]

        positions = np.searchsorted(self._session_nanos, nanos)
        is_session = positions < len(self._session_nanos)
        is_session[is_session] = (
            self._session_nanos[positions[is_session]] == nanos[is_session]
        )
        if not is_session.all():
            bad = pd.DatetimeIndex(nanos[~is_session])
            raise ValueError(
                f"sid {sid}: {len(bad)} dates are not sessions between "
                f"{self._start_session.date()} and {self._end_session.date()}, "
                f"e.g. {bad[0].date()}"
            )

        first = int(positions[0])
        values = np.full((int(positions[-1]) - first + 1, len(FIELDS)), np.nan)
        values[positions - first] = raw

        invalid = np.isinf(values) | (values < 0)
        if invalid.any():
            handle_invalid(
                sid, self._sessions[first:], values, invalid, invalid_data_behavior
            )
            values[invalid] = np.nan

        prices = values[:, : len(PRICE_FIELDS)]
        # A price of 0 means "no data" in zipline.
        prices[prices == 0] = np.nan
        volume = values[:, len(PRICE_FIELDS)]
        values[:, len(PRICE_FIELDS)] = np.round(np.nan_to_num(volume, nan=0.0))
        return first, values

    def _bars_table(self, chunks):
        """One Arrow table from buffered (sid, first position, values) chunks."""
        lengths = [len(values) for _, _, values in chunks]
        sids = np.repeat([sid for sid, _, _ in chunks], lengths).astype("int64")
        positions = np.concatenate(
            [np.arange(lo, lo + n) for (_, lo, _), n in zip(chunks, lengths)]
        )
        values = np.concatenate([values for _, _, values in chunks])
        columns = {
            "sid": sids,
            "day": pa.array(self._session_days[positions], type=pa.date32()),
        }
        for i, field in enumerate(PRICE_FIELDS):
            columns[field] = pa.array(values[:, i], from_pandas=True)
        columns["volume"] = values[:, len(PRICE_FIELDS)].astype("int64")
        return pa.table(columns, schema=BARS_SCHEMA)

    def _write_assets(self, lifetimes, currency_codes):
        sids = np.array([sid for sid, _, _ in lifetimes], dtype="int64")
        if currency_codes is None:
            currencies = [DEFAULT_CURRENCY] * len(sids)
        else:
            currencies = [currency_codes.get(sid, DEFAULT_CURRENCY) for sid in sids]
        table = pa.table(
            {
                "sid": sids,
                "start_session": self._days_array([f for _, f, _ in lifetimes]),
                "end_session": self._days_array([last for _, _, last in lifetimes]),
                "currency": pa.array(currencies, type=pa.string()),
            },
            schema=ASSETS_SCHEMA,
        )
        pq.write_table(table, assets_path(self._rootdir))

    def _days_array(self, positions):
        return pa.array(
            self._session_days[np.asarray(positions, dtype="int64")],
            type=pa.date32(),
        )

    def _write_metadata(self, first_trading_day):
        metadata = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "calendar_name": self._calendar.name,
            "start_session": str(self._start_session.date()),
            "end_session": str(self._end_session.date()),
            "first_trading_day": (
                None if first_trading_day is None else str(first_trading_day.date())
            ),
        }
        write_metadata(self._rootdir, metadata)


def _group_sids(sids):
    """The distinct sids and each row's index into them.

    Writers usually emit sids in ascending order, in which case a linear scan
    finds the groups; otherwise fall back to sorting.
    """
    if len(sids) and (np.diff(sids) >= 0).all():
        starts = np.flatnonzero(np.diff(sids)) + 1
        columns = sids[np.concatenate([[0], starts])]
        cols = np.cumsum(np.concatenate([[0], np.diff(sids) != 0]))
        return columns, cols
    return np.unique(sids, return_inverse=True)


class _Block:
    """One field for one year: a dense (sessions x sids) array."""

    __slots__ = ("columns", "first_pos", "values")

    def __init__(self, values, first_pos, columns):
        self.values = values
        self.first_pos = first_pos
        self.columns = columns

    def column_indexer(self, sids):
        """Column of each sid in ``values``, or -1 if the year has no data."""
        return self.columns.get_indexer(sids)


class ParquetDailyBarReader(CurrencyAwareSessionBarReader):
    """Read daily OHLCV bars written by ParquetDailyBarWriter.

    Parameters
    ----------
    rootdir : str
        The dataset's root directory.
    block_cache_size : int, optional
        How many (year, field) blocks to keep in memory.

    Notes
    -----
    The dataset is opened on first use, so a reader can be created before its
    writer has run. Bundle ingestion relies on this to hand the reader to the
    adjustment writer, which needs close prices to compute dividend ratios.

    See Also
    --------
    zipline.data.parquet_daily_bars.ParquetDailyBarWriter
    """

    def __init__(self, rootdir, block_cache_size=DEFAULT_BLOCK_CACHE_SIZE):
        self._rootdir = rootdir
        self._block_cache_size = block_cache_size
        self._blocks = OrderedDict()
        # Per-year bar layouts, shared by all fields (small: two int arrays).
        self._layouts = {}

    @cached_property
    def _metadata(self):
        return read_metadata(
            self._rootdir, FORMAT_NAME, FORMAT_VERSION, "Parquet daily bar"
        )

    @property
    def data_frequency(self):
        return "daily"

    @cached_property
    def trading_calendar(self):
        return get_calendar(self._metadata["calendar_name"])

    @cached_property
    def sessions(self):
        """The sessions the dataset covers, as a DatetimeIndex."""
        return self.trading_calendar.sessions_in_range(
            pd.Timestamp(self._metadata["start_session"]),
            pd.Timestamp(self._metadata["end_session"]),
        )

    @cached_property
    def _session_nanos(self):
        return epoch_nanos(self.sessions)

    @property
    def last_available_dt(self):
        return self.sessions[-1]

    @cached_property
    def first_trading_day(self):
        """The first session with a bar for any asset, or None."""
        first = self._metadata["first_trading_day"]
        return None if first is None else pd.Timestamp(first)

    @cached_property
    def _assets(self):
        table = pq.read_table(assets_path(self._rootdir))
        frame = table.to_pandas()
        # Store lifetimes as positions in ``sessions``.
        for column in ("start_session", "end_session"):
            nanos = epoch_nanos(pd.DatetimeIndex(frame[column]))
            frame[column] = np.searchsorted(self._session_nanos, nanos)
        return frame.set_index("sid")

    @cached_property
    def _years(self):
        """Map year -> (first position, last position) in ``sessions``."""
        years = self.sessions.year
        out = {}
        for year in np.unique(years):
            positions = np.flatnonzero(years == year)
            out[int(year)] = (int(positions[0]), int(positions[-1]))
        return out

    def _session_position(self, dt):
        pos = np.searchsorted(self._session_nanos, pd.Timestamp(dt).value)
        if (
            pos == len(self._session_nanos)
            or self._session_nanos[pos] != pd.Timestamp(dt).value
        ):
            raise NoDataOnDate(dt)
        return int(pos)

    @cached_property
    def _day_to_position(self):
        """Lookup table from days since the epoch to positions in sessions."""
        days = epoch_nanos(self.sessions) // NANOS_PER_DAY
        table = np.full(days[-1] - days[0] + 1, -1, dtype="int64")
        table[days - days[0]] = np.arange(len(days))
        return days[0], table

    def _layout(self, year):
        """Where each stored bar of ``year`` goes in that year's blocks.

        Shared by every field, so a field's block only reads its own column.
        Returns (rows, cols, columns): the row and column of each stored bar,
        and the sids that label the columns.
        """
        try:
            return self._layouts[year]
        except KeyError:
            pass
        table = pq.read_table(_year_path(self._rootdir, year), columns=["sid", "day"])
        sids = table.column("sid").to_numpy()
        # date32 arrives as datetime64[D]; view it as days since the epoch.
        days = table.column("day").to_numpy().astype("datetime64[D]").view("int64")
        first_day, day_to_position = self._day_to_position
        first_pos = self._years[year][0]
        rows = day_to_position[days - first_day] - first_pos
        columns, cols = _group_sids(sids)
        layout = self._layouts[year] = (rows, cols, pd.Index(columns))
        return layout

    def _block(self, year, field):
        key = (year, field)
        try:
            self._blocks.move_to_end(key)
            return self._blocks[key]
        except KeyError:
            pass

        first_pos, last_pos = self._years[year]
        n_rows = last_pos - first_pos + 1
        missing = 0.0 if field == "volume" else np.nan
        path = _year_path(self._rootdir, year)
        if os.path.exists(path):
            rows, cols, columns = self._layout(year)
            column = pq.read_table(path, columns=[field]).column(field)
            values = np.full((n_rows, len(columns)), missing)
            values[rows, cols] = column.to_numpy(zero_copy_only=False)
            if field != "volume":
                # Nulls come through as NaN already; keep 0 meaning missing.
                values[values == 0] = np.nan
        else:
            columns = pd.Index([], dtype="int64")
            values = np.full((n_rows, 0), missing)

        block = _Block(values, first_pos, columns)
        self._blocks[key] = block
        if len(self._blocks) > self._block_cache_size:
            self._blocks.popitem(last=False)
        return block

    def load_raw_arrays(self, columns, start_date, end_date, assets):
        start_pos = self._session_position(start_date)
        end_pos = self._session_position(end_date)
        sids = as_sids(assets)
        if not (self._assets.index.get_indexer(sids) >= 0).any():
            raise ValueError("At least one valid asset id is required.")
        n_days = end_pos - start_pos + 1
        years = sorted(
            year
            for year, (first, last) in self._years.items()
            if first <= end_pos and last >= start_pos
        )

        results = []
        for field in columns:
            missing = 0.0 if field == "volume" else np.nan
            out = np.full((n_days, len(sids)), missing)
            for year in years:
                block = self._block(year, field)
                lo = max(start_pos, block.first_pos)
                hi = min(end_pos, block.first_pos + len(block.values) - 1)
                cols = block.column_indexer(sids)
                known = cols >= 0
                if not known.any():
                    continue
                out[lo - start_pos : hi - start_pos + 1, known] = block.values[
                    lo - block.first_pos : hi - block.first_pos + 1
                ][:, cols[known]]
            results.append(out)
        return results

    def _lifetime(self, sid):
        try:
            row = self._assets.loc[sid]
        except KeyError:
            raise NoDataOnDate(f"No data for sid={sid}") from None
        return int(row["start_session"]), int(row["end_session"])

    def _checked_position(self, sid, dt):
        """Position of ``dt`` in ``sessions``, within ``sid``'s lifetime."""
        pos = self._session_position(dt)
        start, end = self._lifetime(sid)
        if pos < start:
            raise NoDataBeforeDate(f"No data on or before day={dt} for sid={sid}")
        if pos > end:
            raise NoDataAfterDate(f"No data on or after day={dt} for sid={sid}")
        return pos

    def _value_at(self, sid, pos, field):
        year = int(self.sessions[pos].year)
        block = self._block(year, field)
        col = block.column_indexer([sid])[0]
        if col < 0:
            return 0.0 if field == "volume" else np.nan
        return block.values[pos - block.first_pos, col]

    def get_value(self, sid, dt, field):
        sid = int(sid)
        pos = self._checked_position(sid, dt)
        return self._value_at(sid, pos, field)

    def get_last_traded_dt(self, asset, dt):
        sid = int(asset)
        try:
            start, end = self._lifetime(sid)
            pos = min(self._session_position(dt), end)
        except NoDataOnDate:
            return pd.NaT
        while pos >= start:
            year = int(self.sessions[pos].year)
            block = self._block(year, "volume")
            col = block.column_indexer([sid])[0]
            first = max(start, block.first_pos)
            if col >= 0:
                rows = slice(first - block.first_pos, pos - block.first_pos + 1)
                volumes = block.values[rows, col]
                traded = np.flatnonzero(volumes)
                if len(traded):
                    return self.sessions[first + traded[-1]]
            pos = first - 1
        return pd.NaT

    def currency_codes(self, sids):
        assets = self._assets
        known = assets.index.get_indexer(as_sids(sids))
        currencies = assets["currency"].to_numpy()
        out = np.full(len(known), None, dtype=object)
        out[known >= 0] = currencies[known[known >= 0]]
        return out
