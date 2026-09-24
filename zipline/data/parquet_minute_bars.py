"""Minute OHLCV bars stored as a Parquet dataset.

On-disk layout, under a root directory::

    _metadata.json                   format version, calendar and session range
    _assets.parquet                  per-asset first and last minute with a bar
    year=2023/month=01/data.parquet  the bars of each month's sessions
    year=2023/month=02/data.parquet
    ...

Bars are stored in long format with columns ``sid`` (int64), ``dt``
(timestamp[ns, UTC]), ``open``/``high``/``low``/``close`` (float64) and
``volume`` (int64). Only minutes with a bar are stored: a missing row means no
trade, a missing price is null and a missing volume is 0. Bars are partitioned
by the month of their session, and within a month each asset's bars are
contiguous and sorted, sids are sorted within each row group, and an asset's
bars for a month never span two row groups. The partitions use Hive-style
names, so the dataset can also be read directly with ``pyarrow.dataset``,
pandas, DuckDB and similar tools.

The reader numbers the calendar's trading minutes over the dataset's sessions,
so unlike the bcolz format it needs no fixed number of minutes per day, and
early closes and breaks need no special handling. It reads whole row groups,
splits them into per-asset arrays of (minute position, OHLCV), and keeps
recently used row groups in a cache bounded by size.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pandas.api.typing import NaTType

    from zipline.assets import Asset
    from zipline.utils.calendar_utils import ExchangeCalendar

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from zipline.data._parquet import (
    FIELDS,
    PRICE_FIELDS,
    as_sids,
    assets_path,
    directory_has_files,
    epoch_nanos,
    handle_invalid,
    read_metadata,
    write_metadata,
)
from zipline.data.bar_reader import NoDataForSid, NoDataOnDate
from zipline.data.minute_bars import MinuteBarReader
from zipline.utils.calendar_utils import get_calendar
from zipline.utils.cli import maybe_show_progress
from zipline.utils.date_utils import to_session_label

FORMAT_NAME = "zipline.minute_bars.parquet"
FORMAT_VERSION = 1

BARS_SCHEMA = pa.schema(
    [("sid", pa.int64()), ("dt", pa.timestamp("ns", tz="UTC"))]
    + [(field, pa.float64()) for field in PRICE_FIELDS]
    + [("volume", pa.int64())]
)
ASSETS_SCHEMA = pa.schema(
    [
        ("sid", pa.int64()),
        ("first_minute", pa.timestamp("ns", tz="UTC")),
        ("last_minute", pa.timestamp("ns", tz="UTC")),
    ]
)

# Rows buffered per month before they are written out as a row group.
DEFAULT_ROW_GROUP_SIZE = 131_072
# Rows buffered across all months before every month's buffer is written out,
# which bounds the writer's memory when each asset spans many months.
DEFAULT_MAX_BUFFERED_ROWS = 4_000_000
# Bytes of decoded row groups the reader keeps in memory.
DEFAULT_BLOCK_CACHE_BYTES = 256 * 2**20

NANOS_PER_MINUTE = 60 * 10**9

FIELD_INDEX = {field: i for i, field in enumerate(FIELDS)}
VOLUME = FIELD_INDEX["volume"]

_NO_ROWS = (np.empty(0, dtype="int32"), np.empty((0, len(FIELDS))))
# Decoded size of a stored bar: an int32 minute position and five float64s.
_DECODED_BYTES_PER_ROW = 4 + 8 * len(FIELDS)


def _month_path(rootdir, year, month):
    return os.path.join(rootdir, f"year={year}", f"month={month:02d}", "data.parquet")


class _MinuteIndex:
    """The trading minutes of a range of sessions, and the months they fall in.

    Minutes are identified by their position in ``nanos``. A session's minutes
    belong to the month of the session, even when they fall on another day.
    """

    def __init__(self, calendar, start_session, end_session):
        sessions = calendar.sessions_in_range(start_session, end_session)
        first_minutes = calendar.first_minutes[sessions]
        minutes = calendar.minutes_in_range(
            first_minutes.iloc[0], calendar.session_last_minute(sessions[-1])
        )
        self.minutes = minutes
        self.nanos = epoch_nanos(minutes)
        session_starts = np.searchsorted(
            self.nanos, epoch_nanos(pd.DatetimeIndex(first_minutes))
        )
        month_keys = sessions.year * 12 + sessions.month - 1
        new_month = np.flatnonzero(np.diff(month_keys, prepend=-1))
        self.month_starts = session_starts[new_month]
        self.months = [
            (int(sessions[i].year), int(sessions[i].month)) for i in new_month
        ]

    def month_range(self, month):
        """The first and last minute positions of ``month``."""
        end = (
            self.month_starts[month + 1]
            if month + 1 < len(self.month_starts)
            else len(self.nanos)
        )
        return int(self.month_starts[month]), int(end) - 1

    def positions(self, month, nanos):
        """Positions of minutes of ``month``, given as epoch nanos.

        Minutes that are not trading minutes of ``month`` get -1. Uses a table
        indexed by wall-clock minute since the month's first trading minute,
        which is much faster than a binary search; the table is small and
        cheap to build, so it isn't kept.
        """
        first, last = self.month_range(month)
        month_nanos = self.nanos[first : last + 1]
        first_nanos = month_nanos[0]
        offsets = (month_nanos - first_nanos) // NANOS_PER_MINUTE
        table = np.full(offsets[-1] + 1, -1, dtype="int32")
        table[offsets] = np.arange(first, last + 1)

        offsets = nanos - first_nanos
        in_table = (offsets >= 0) & (offsets < len(table) * NANOS_PER_MINUTE)
        out = np.full(len(nanos), -1, dtype="int32")
        out[in_table] = table[offsets[in_table] // NANOS_PER_MINUTE]
        # Stored minutes are whole minutes; anything else isn't a trading minute.
        out[offsets % NANOS_PER_MINUTE != 0] = -1
        return out

    def month_of(self, positions):
        """The month of each minute position, as an index into ``months``."""
        return np.searchsorted(self.month_starts, positions, side="right") - 1

    def minute(self, pos):
        return self.minutes[pos]


class ParquetMinuteBarWriter:
    """Write minute OHLCV bars to a Parquet dataset.

    Parameters
    ----------
    rootdir : str
        The directory to write the dataset into. It must not already contain
        a dataset.
    calendar : ExchangeCalendar
        The calendar the minutes belong to.
    start_session, end_session : pd.Timestamp
        The first and last sessions of the dataset.
    row_group_size : int, optional
        The number of rows buffered per month before being written.
    max_buffered_rows : int, optional
        The number of rows buffered across all months before every month's
        buffer is written.

    Notes
    -----
    Readers are fastest when sids are written in ascending order, since each
    row group then covers a narrow range of sids.

    See Also
    --------
    zipline.data.parquet_minute_bars.ParquetMinuteBarReader
    """

    def __init__(
        self,
        rootdir,
        calendar,
        start_session,
        end_session,
        row_group_size=DEFAULT_ROW_GROUP_SIZE,
        max_buffered_rows=DEFAULT_MAX_BUFFERED_ROWS,
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
        self._max_buffered_rows = max_buffered_rows
        self._index = _MinuteIndex(calendar, start_session, end_session)

    @property
    def exists(self):
        """Whether ``rootdir`` already holds files, e.g. a dataset."""
        return directory_has_files(self._rootdir)

    def write(self, data, show_progress=False, invalid_data_behavior="warn"):
        """Write minute bars.

        Parameters
        ----------
        data : iterable[tuple[int, pd.DataFrame]]
            Pairs of sid and a frame of that asset's bars, indexed by minute,
            with open, high, low, close and volume columns. Minutes must be
            trading minutes of the calendar; naive minutes are taken as UTC.
            Each sid may appear only once.
        show_progress : bool, optional
            Whether to show a progress bar.
        invalid_data_behavior : {'warn', 'raise', 'ignore'}, optional
            What to do about negative or infinite values, which are stored as
            missing data.
        """
        if self.exists:
            raise ValueError(f"{self._rootdir} is not empty")
        os.makedirs(self._rootdir, exist_ok=True)

        # Per month: buffered (sid, positions, values) chunks and their rows.
        buffers = {}
        buffered_rows = {}
        writers = {}
        lifetimes = {}

        def flush(month):
            table = self._bars_table(buffers.pop(month))
            del buffered_rows[month]
            if month not in writers:
                path = _month_path(self._rootdir, *self._index.months[month])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                writers[month] = pq.ParquetWriter(path, BARS_SCHEMA, compression="zstd")
            # One row group per flush keeps each sid's month in one group.
            writers[month].write_table(table, row_group_size=len(table))

        ctx = maybe_show_progress(
            iter(data),
            show_progress=show_progress,
            label="Merging minute equity files:",
        )
        try:
            with ctx as it:
                for sid, frame in it:
                    sid = int(sid)
                    if sid in lifetimes:
                        raise ValueError(f"sid {sid} appears more than once")
                    prepared = self._prepare(sid, frame, invalid_data_behavior)
                    if prepared is None:
                        lifetimes[sid] = None
                        continue
                    positions, values = prepared
                    lifetimes[sid] = (positions[0], positions[-1])

                    months = self._index.month_of(positions)
                    splits = np.flatnonzero(np.diff(months)) + 1
                    for month, month_positions, month_values in zip(
                        months[np.concatenate([[0], splits])],
                        np.split(positions, splits),
                        np.split(values, splits),
                    ):
                        month = int(month)
                        buffers.setdefault(month, []).append(
                            (sid, month_positions, month_values)
                        )
                        buffered_rows[month] = buffered_rows.get(month, 0) + len(
                            month_positions
                        )
                        if buffered_rows[month] >= self._row_group_size:
                            flush(month)
                    if sum(buffered_rows.values()) >= self._max_buffered_rows:
                        for month in list(buffers):
                            flush(month)
            for month in list(buffers):
                flush(month)
        finally:
            for writer in writers.values():
                writer.close()

        self._write_assets(lifetimes)
        self._write_metadata()

    def _prepare(self, sid, frame, invalid_data_behavior):
        """Validate one asset's bars.

        Returns the minute positions of the asset's bars and a matching
        (bars, OHLCV) float64 array, or None if there are no bars.
        """
        missing = set(FIELDS) - set(frame.columns)
        if missing:
            raise ValueError(f"sid {sid}: missing columns {sorted(missing)}")
        if frame.empty:
            return None

        nanos = epoch_nanos(pd.DatetimeIndex(frame.index))
        values = frame[list(FIELDS)].to_numpy(dtype="float64", copy=True)
        if not (np.diff(nanos) > 0).all():
            # Sort by minute, keeping the last of any duplicated minutes.
            order = np.argsort(nanos, kind="stable")
            nanos, values = nanos[order], values[order]
            keep = np.append(nanos[1:] != nanos[:-1], True)
            nanos, values = nanos[keep], values[keep]

        minute_nanos = self._index.nanos
        positions = np.searchsorted(minute_nanos, nanos)
        is_minute = positions < len(minute_nanos)
        is_minute[is_minute] = minute_nanos[positions[is_minute]] == nanos[is_minute]
        if not is_minute.all():
            bad = pd.DatetimeIndex(nanos[~is_minute], tz="UTC")
            raise ValueError(
                f"sid {sid}: {len(bad)} minutes are not trading minutes of "
                f"sessions {self._start_session.date()} to "
                f"{self._end_session.date()}, e.g. {bad[0]}"
            )

        invalid = np.isinf(values) | (values < 0)
        if invalid.any():
            labels = pd.DatetimeIndex(nanos, tz="UTC")
            handle_invalid(sid, labels, values, invalid, invalid_data_behavior)
            values[invalid] = np.nan

        prices = values[:, : len(PRICE_FIELDS)]
        # A price of 0 means "no data" in zipline.
        prices[prices == 0] = np.nan
        values[:, VOLUME] = np.round(np.nan_to_num(values[:, VOLUME], nan=0.0))

        has_bar = (values[:, VOLUME] > 0) | ~np.isnan(prices).all(axis=1)
        if not has_bar.any():
            return None
        return positions[has_bar], values[has_bar]

    def _bars_table(self, chunks):
        """One Arrow table from buffered (sid, positions, values) chunks."""
        chunks.sort(key=lambda chunk: chunk[0])
        sids = np.repeat(
            np.array([sid for sid, _, _ in chunks], dtype="int64"),
            [len(positions) for _, positions, _ in chunks],
        )
        positions = np.concatenate([positions for _, positions, _ in chunks])
        values = np.concatenate([values for _, _, values in chunks])
        columns = {
            "sid": sids,
            "dt": pa.array(
                self._index.nanos[positions], type=BARS_SCHEMA.field("dt").type
            ),
        }
        for i, field in enumerate(PRICE_FIELDS):
            columns[field] = pa.array(values[:, i], from_pandas=True)
        columns["volume"] = values[:, VOLUME].astype("int64")
        return pa.table(columns, schema=BARS_SCHEMA)

    def _write_assets(self, lifetimes):
        sids = np.fromiter(lifetimes, dtype="int64")
        order = np.argsort(sids)
        dt_type = ASSETS_SCHEMA.field("first_minute").type

        def minutes(which):
            nanos = [
                None if lifetime is None else int(self._index.nanos[lifetime[which]])
                for lifetime in lifetimes.values()
            ]
            return pa.array(nanos, type=pa.int64()).cast(dt_type).take(order)

        table = pa.table(
            {
                "sid": sids[order],
                "first_minute": minutes(0),
                "last_minute": minutes(1),
            },
            schema=ASSETS_SCHEMA,
        )
        pq.write_table(table, assets_path(self._rootdir))

    def _write_metadata(self):
        write_metadata(
            self._rootdir,
            {
                "format": FORMAT_NAME,
                "version": FORMAT_VERSION,
                "calendar_name": self._calendar.name,
                "start_session": str(self._start_session.date()),
                "end_session": str(self._end_session.date()),
            },
        )


class _Block:
    """One decoded row group: each sid's minute positions and OHLCV values."""

    __slots__ = (
        "first_pos",
        "key",
        "lookups",
        "month_len",
        "nbytes",
        "positions",
        "spans",
        "values",
    )

    def __init__(self, key, table, index):
        self.key = month, _ = key
        self.first_pos, last_pos = index.month_range(month)
        self.month_len = last_pos - self.first_pos + 1
        # sid -> the row of each of the month's minutes; see add_lookup().
        self.lookups = {}
        sids = table.column("sid").to_numpy()
        nanos = table.column("dt").to_numpy().view("int64")
        positions = index.positions(month, nanos)
        if (positions < 0).any():
            bad = pd.Timestamp(nanos[np.argmax(positions < 0)], tz="UTC")
            raise ValueError(
                f"The dataset has bars at minutes that are not trading minutes "
                f"of its calendar's sessions in {index.months[month]}, e.g. "
                f"{bad}. Was it written with a different calendar?"
            )
        values = np.column_stack(
            [
                table.column(field).to_numpy(zero_copy_only=False)
                for field in PRICE_FIELDS
            ]
            + [table.column("volume").to_numpy().astype("float64")]
        )
        if (
            len(sids) > 1
            and not (
                (np.diff(sids) > 0) | ((np.diff(sids) == 0) & (np.diff(positions) > 0))
            ).all()
        ):
            order = np.lexsort((positions, sids))
            sids, positions, values = sids[order], positions[order], values[order]

        starts = np.concatenate([[0], np.flatnonzero(np.diff(sids)) + 1])
        ends = np.append(starts[1:], len(sids))
        self.spans = {
            int(sid): (int(start), int(end))
            for sid, start, end in zip(sids[starts], starts, ends)
        }
        self.positions = positions
        self.values = values
        self.nbytes = positions.nbytes + values.nbytes

    def rows(self, sid):
        """``sid``'s (positions, values), or None if the block lacks it."""
        span = self.spans.get(sid)
        if span is None:
            return None
        start, end = span
        return self.positions[start:end], self.values[start:end]

    def add_lookup(self, sid):
        """Build the row in ``values`` of each minute of the month for ``sid``.

        Minutes without a bar map to -1. Lookups are built on first use and
        kept in ``lookups``, so single-value reads index an array instead of
        searching ``sid``'s positions.
        """
        start, end = self.spans[sid]
        rows = np.full(self.month_len, -1, dtype="int32")
        rows[self.positions[start:end] - self.first_pos] = np.arange(start, end)
        self.lookups[sid] = rows
        self.nbytes += rows.nbytes
        return rows


class ParquetMinuteBarReader(MinuteBarReader):
    """Read minute OHLCV bars written by ParquetMinuteBarWriter.

    Parameters
    ----------
    rootdir : str
        The dataset's root directory.
    block_cache_bytes : int, optional
        How many bytes of decoded row groups to keep in memory.

    Notes
    -----
    The dataset is opened on first use, so a reader can be created before its
    writer has run.

    See Also
    --------
    zipline.data.parquet_minute_bars.ParquetMinuteBarWriter
    """

    def __init__(
        self, rootdir: str, block_cache_bytes: int = DEFAULT_BLOCK_CACHE_BYTES
    ) -> None:
        self._rootdir = rootdir
        self._block_cache_bytes = block_cache_bytes
        # (month, row group) -> _Block, least recently used first.
        self._blocks = OrderedDict()
        self._cached_bytes = 0
        # (month, sid) -> the cached block holding sid's bars, or None if
        # sid has no bars that month.
        self._sid_blocks = {}
        # month -> (ParquetFile, per-row-group min sids, max sids) or None.
        self._month_files = {}
        # The last minute located, as (epoch nanos, (position, month)).
        self._last_located = (None, None)

    @cached_property
    def _metadata(self):
        return read_metadata(
            self._rootdir, FORMAT_NAME, FORMAT_VERSION, "Parquet minute bar"
        )

    @cached_property
    def trading_calendar(self) -> ExchangeCalendar:
        return get_calendar(self._metadata["calendar_name"])

    @cached_property
    def _index(self):
        return _MinuteIndex(
            self.trading_calendar,
            pd.Timestamp(self._metadata["start_session"]),
            pd.Timestamp(self._metadata["end_session"]),
        )

    @property
    def first_trading_day(self) -> pd.Timestamp:
        """The first session of the dataset."""
        return pd.Timestamp(self._metadata["start_session"])

    @cached_property
    def last_available_dt(self) -> pd.Timestamp:
        """The last trading minute of the dataset."""
        return self._index.minute(-1)

    @cached_property
    def _assets(self):
        """Map sid -> (first, last) minute position with a bar, or None."""
        table = pq.read_table(assets_path(self._rootdir))
        minute_nanos = self._index.nanos
        out = {}
        for sid, first, last in zip(
            table.column("sid").to_pylist(),
            table.column("first_minute").cast(pa.int64()).to_pylist(),
            table.column("last_minute").cast(pa.int64()).to_pylist(),
        ):
            out[sid] = (
                None
                if first is None
                else (
                    int(np.searchsorted(minute_nanos, first)),
                    int(np.searchsorted(minute_nanos, last)),
                )
            )
        return out

    def _lifetime(self, sid):
        try:
            return self._assets[sid]
        except KeyError:
            raise NoDataForSid(f"No minute data for sid {sid}.") from None

    def _locate(self, dt):
        """The position and month of the trading minute ``dt``."""
        value = _nanos(dt)
        last_value, located = self._last_located
        if value == last_value:
            # Simulations look up many assets at the same minute.
            return located
        minute_nanos = self._index.nanos
        pos = int(minute_nanos.searchsorted(value))
        if pos == len(minute_nanos) or minute_nanos[pos] != value:
            raise NoDataOnDate(f"{dt} is not a trading minute of the dataset")
        located = pos, int(self._index.month_of(pos))
        self._last_located = (value, located)
        return located

    def _month_file(self, month):
        try:
            return self._month_files[month]
        except KeyError:
            pass
        path = _month_path(self._rootdir, *self._index.months[month])
        info = None
        if os.path.exists(path):
            parquet_file = pq.ParquetFile(path)
            metadata = parquet_file.metadata
            sid_column = parquet_file.schema_arrow.get_field_index("sid")
            stats = [
                metadata.row_group(i).column(sid_column).statistics
                for i in range(metadata.num_row_groups)
            ]
            info = (
                parquet_file,
                np.array([s.min for s in stats], dtype="int64"),
                np.array([s.max for s in stats], dtype="int64"),
            )
        self._month_files[month] = info
        return info

    def _row_groups_for(self, month, sids):
        """The row groups of ``month`` whose sid range covers any of ``sids``."""
        info = self._month_file(month)
        if info is None:
            return []
        _, mins, maxs = info
        sids = np.asarray(sids)[:, None]
        return np.flatnonzero(((mins <= sids) & (maxs >= sids)).any(axis=0)).tolist()

    def _read_batches(self, month, row_groups):
        """Split ``row_groups`` into reads whose decoded size fits the cache."""
        metadata = self._month_file(month)[0].metadata
        batch, batch_bytes = [], 0
        for rg in row_groups:
            nbytes = metadata.row_group(rg).num_rows * _DECODED_BYTES_PER_ROW
            if batch and batch_bytes + nbytes > self._block_cache_bytes:
                yield batch
                batch, batch_bytes = [], 0
            batch.append(rg)
            batch_bytes += nbytes
        if batch:
            yield batch

    def _blocks_for(self, month, sids):
        """Yield the blocks of ``month``'s row groups that may hold ``sids``.

        Cached blocks come first. The rest are decoded in reads that fit the
        cache, so a large request streams through the cache instead of
        decoding everything at once.
        """
        row_groups = self._row_groups_for(month, sids)
        missing = []
        for rg in row_groups:
            block = self._blocks.get((month, rg))
            if block is None:
                missing.append(rg)
            else:
                self._blocks.move_to_end(block.key)
                yield block
        if not missing:
            return
        parquet_file = self._month_file(month)[0]
        for batch in self._read_batches(month, missing):
            table = parquet_file.read_row_groups(batch)
            blocks, offset = [], 0
            for rg in batch:
                n_rows = parquet_file.metadata.row_group(rg).num_rows
                block = _Block((month, rg), table.slice(offset, n_rows), self._index)
                offset += n_rows
                self._blocks[block.key] = block
                self._cached_bytes += block.nbytes
                blocks.append(block)
            self._evict()
            yield from blocks

    def _evict(self):
        """Evict least recently used blocks, but always keep the newest."""
        while self._cached_bytes > self._block_cache_bytes and len(self._blocks) > 1:
            (month, _), evicted = self._blocks.popitem(last=False)
            self._cached_bytes -= evicted.nbytes
            for sid in evicted.spans:
                self._sid_blocks.pop((month, sid), None)

    def _sid_block(self, month, sid):
        """The cached block holding ``sid``'s bars for ``month``, or None."""
        key = (month, sid)
        try:
            block = self._sid_blocks[key]
        except KeyError:
            pass
        else:
            if block is not None:
                self._blocks.move_to_end(block.key)
            return block
        found = None
        for block in self._blocks_for(month, [sid]):
            if sid in block.spans:
                found = block
                break
        self._sid_blocks[key] = found
        return found

    def _sid_rows(self, month, sid):
        """``sid``'s (positions, values) in ``month``, possibly empty."""
        block = self._sid_block(month, sid)
        return _NO_ROWS if block is None else block.rows(sid)

    def get_value(self, sid: int, dt: pd.Timestamp, field: str) -> float:
        """Retrieve the value of ``field`` for ``sid`` at the minute ``dt``.

        Returns NaN for a missing price and 0 for a missing volume.
        """
        sid = int(sid)
        if sid not in self._assets:
            raise NoDataForSid(f"No minute data for sid {sid}.")
        pos, month = self._locate(dt)
        block = self._sid_block(month, sid)
        if block is not None:
            rows = block.lookups.get(sid)
            if rows is None:
                rows = block.add_lookup(sid)
                self._cached_bytes += rows.nbytes
                self._evict()
            row = rows[pos - block.first_pos]
            if row >= 0:
                return block.values[row, FIELD_INDEX[field]]
        return 0.0 if field == "volume" else np.nan

    def get_last_traded_dt(
        self, asset: Asset | int, dt: pd.Timestamp
    ) -> pd.Timestamp | NaTType:
        """The last minute at or before ``dt`` with volume for ``asset``."""
        sid = int(asset)
        lifetime = self._assets.get(sid)
        if lifetime is None:
            return pd.NaT
        first, last = lifetime
        pos = int(np.searchsorted(self._index.nanos, _nanos(dt), "right"))
        pos = min(pos - 1, last)
        if pos < first:
            return pd.NaT
        first_month = int(self._index.month_of(first))
        for month in range(int(self._index.month_of(pos)), first_month - 1, -1):
            positions, values = self._sid_rows(month, sid)
            n = positions.searchsorted(pos, side="right")
            if n and values[n - 1, VOLUME] > 0:
                # The usual case: the latest bar was a trade.
                return self._index.minute(positions[n - 1])
            traded = np.flatnonzero(values[:n, VOLUME] > 0)
            if len(traded):
                return self._index.minute(positions[traded[-1]])
        return pd.NaT

    def load_raw_arrays(
        self,
        columns: Sequence[str],
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        assets: Sequence[int] | np.ndarray,
    ) -> list[np.ndarray]:
        """Load (minutes, assets) float64 arrays of each field in ``columns``.

        The rows are the trading minutes from ``start_date`` through
        ``end_date``, which need not be trading minutes themselves (e.g. the
        first minute of a lunch break). Missing prices are NaN and missing
        volumes are 0.
        """
        minute_nanos = self._index.nanos
        start_nanos, end_nanos = _nanos(start_date), _nanos(end_date)
        if start_nanos < minute_nanos[0] or end_nanos > minute_nanos[-1]:
            raise NoDataOnDate(
                f"{start_date} to {end_date} is not within the dataset's minutes"
            )
        start = int(minute_nanos.searchsorted(start_nanos))
        end = int(minute_nanos.searchsorted(end_nanos, side="right")) - 1
        sids = as_sids(assets)
        for sid in sids:
            self._lifetime(int(sid))
        fields = [FIELD_INDEX[column] for column in columns]
        n_minutes = max(end - start + 1, 0)
        results = [
            np.full((n_minutes, len(sids)), 0.0 if column == "volume" else np.nan)
            for column in columns
        ]
        if not n_minutes:
            return results

        def scatter(block, sid, cols):
            positions, values = block.rows(sid)
            lo = positions.searchsorted(start)
            hi = positions.searchsorted(end, side="right")
            if lo == hi:
                return
            rows = positions[lo:hi] - start
            for out, field in zip(results, fields):
                out[rows[:, None], cols] = values[lo:hi, field][:, None]

        month_of = self._index.month_of
        for month in range(int(month_of(start)), int(month_of(end)) + 1):
            # sid -> its columns in the output, for sids not known to be cached.
            pending = {}
            for j, sid in enumerate(sids.tolist()):
                key = (month, sid)
                if key in self._sid_blocks:
                    block = self._sid_blocks[key]
                    if block is not None:
                        self._blocks.move_to_end(block.key)
                        scatter(block, sid, [j])
                else:
                    pending.setdefault(sid, []).append(j)
            if not pending:
                continue
            for block in self._blocks_for(month, list(pending)):
                for sid in block.spans.keys() & pending.keys():
                    self._sid_blocks[(month, sid)] = block
                    scatter(block, sid, pending.pop(sid))
            for sid in pending:
                self._sid_blocks[(month, sid)] = None
        return results


def _nanos(dt):
    """Epoch nanoseconds of a timestamp; naive timestamps are taken as UTC."""
    return dt.value if isinstance(dt, pd.Timestamp) else pd.Timestamp(dt).value
