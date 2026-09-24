# Copyright 2015 Quantopian, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import warnings
from collections.abc import Hashable
from functools import cached_property, partial

import numpy as np
from bcolz import carray, ctable
from numpy import (
    array,
    full,
    iinfo,
    nan,
)
from pandas import (
    DatetimeIndex,
    NaT,
    Timestamp,
    read_csv,
    to_datetime,
)
from toolz import compose

from zipline.data.bar_reader import (
    NoDataAfterDate,
    NoDataBeforeDate,
    NoDataOnDate,
)
from zipline.data.session_bars import CurrencyAwareSessionBarReader
from zipline.utils.calendar_utils import get_calendar
from zipline.utils.cli import maybe_show_progress
from zipline.utils.input_validation import expect_element
from zipline.utils.numpy_utils import float64_dtype, iNaT, uint32_dtype

OHLC = frozenset(["open", "high", "low", "close"])
US_EQUITY_PRICING_BCOLZ_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "day",
    "id",
)

UINT32_MAX = iinfo(np.uint32).max


def check_uint32_safe(value, colname):
    if value >= UINT32_MAX:
        raise ValueError(f"Value {value} from column '{colname}' is too large")


@expect_element(invalid_data_behavior={"warn", "raise", "ignore"})
def winsorise_uint32(df, invalid_data_behavior, column, *columns):
    """Drops any record where a value would not fit into a uint32.

    Parameters
    ----------
    df : pd.DataFrame
        The dataframe to winsorise.
    invalid_data_behavior : {'warn', 'raise', 'ignore'}
        What to do when data is outside the bounds of a uint32.
    *columns : iterable[str]
        The names of the columns to check.

    Returns
    -------
    truncated : pd.DataFrame
        ``df`` with values that do not fit into a uint32 zeroed out.
    """
    columns = list((column,) + columns)
    mask = df[columns] > UINT32_MAX

    if invalid_data_behavior != "ignore":
        mask |= df[columns].isnull()
    else:
        # we are not going to generate a warning or error for this so just use
        # nan_to_num
        df[columns] = np.nan_to_num(df[columns])

    mv = mask.values
    if mv.any():
        if invalid_data_behavior == "raise":
            raise ValueError(
                f"{mv.sum()} values out of bounds for uint32: {df[mask.any(axis=1)]!r}"
            )
        if invalid_data_behavior == "warn":
            warnings.warn(
                f"Ignoring {mv.sum()} values because they are out of bounds for"
                f" uint32: {df[mask.any(axis=1)]!r}",
                stacklevel=3,  # one extra frame for `expect_element`
            )

    df[mask] = 0
    return df


def _check_asset_ids(iterator, assets):
    """Yield from ``iterator``, raising for asset ids not in ``assets``."""
    for asset_id, table in iterator:
        if asset_id not in assets:
            raise ValueError(f"unknown asset id {asset_id!r}")
        yield asset_id, table


class BcolzDailyBarWriter:
    """
    Class capable of writing daily OHLCV data to disk in a format that can
    be read efficiently by BcolzDailyOHLCVReader.

    Parameters
    ----------
    filename : str
        The location at which we should write our output.
    calendar : zipline.utils.calendar.trading_calendar
        Calendar to use to compute asset calendar offsets.
    start_session: pd.Timestamp
        Midnight (tz-naive) session label.
    end_session: pd.Timestamp
        Midnight (tz-naive) session label.

    See Also
    --------
    zipline.data.bcolz_daily_bars.BcolzDailyBarReader
    """

    _csv_dtypes: dict[Hashable, np.dtype] = {
        "open": float64_dtype,
        "high": float64_dtype,
        "low": float64_dtype,
        "close": float64_dtype,
        "volume": float64_dtype,
    }

    def __init__(self, filename, calendar, start_session, end_session):
        self._filename = filename

        if start_session != end_session:
            if not calendar.is_session(start_session):
                raise ValueError(f"Start session {start_session} is invalid!")
            if not calendar.is_session(end_session):
                raise ValueError(f"End session {end_session} is invalid!")

        self._start_session = start_session
        self._end_session = end_session

        self._calendar = calendar

    @property
    def progress_bar_message(self):
        return "Merging daily equity files:"

    def progress_bar_item_show_func(self, value):
        return value if value is None else str(value[0])

    def write(
        self, data, assets=None, show_progress=False, invalid_data_behavior="warn"
    ):
        """
        Parameters
        ----------
        data : iterable[tuple[int, pandas.DataFrame or bcolz.ctable]]
            The data chunks to write. Each chunk should be a tuple of sid
            and the data for that asset.
        assets : set[int], optional
            The assets that should be in ``data``. If this is provided
            we will check ``data`` against the assets and provide better
            progress information.
        show_progress : bool, optional
            Whether or not to show a progress bar while writing.
        invalid_data_behavior : {'warn', 'raise', 'ignore'}, optional
            What to do when data is encountered that is outside the range of
            a uint32.

        Returns
        -------
        table : bcolz.ctable
            The newly-written table.
        """
        ctx = maybe_show_progress(
            ((sid, self.to_ctable(df, invalid_data_behavior)) for sid, df in data),
            show_progress=show_progress,
            item_show_func=self.progress_bar_item_show_func,
            label=self.progress_bar_message,
            length=len(assets) if assets is not None else None,
        )
        with ctx as it:
            return self._write_internal(it, assets)

    def write_csvs(self, asset_map, show_progress=False, invalid_data_behavior="warn"):
        """Read CSVs as DataFrames from our asset map.

        Parameters
        ----------
        asset_map : dict[int -> str]
            A mapping from asset id to file path with the CSV data for that
            asset
        show_progress : bool
            Whether or not to show a progress bar while writing.
        invalid_data_behavior : {'warn', 'raise', 'ignore'}
            What to do when data is encountered that is outside the range of
            a uint32.
        """
        read = partial(
            read_csv,
            parse_dates=["day"],
            index_col="day",
            dtype=self._csv_dtypes,
        )
        return self.write(
            ((asset, read(path)) for asset, path in asset_map.items()),
            assets=asset_map.keys(),
            show_progress=show_progress,
            invalid_data_behavior=invalid_data_behavior,
        )

    def _write_internal(self, iterator, assets):
        """
        Internal implementation of write.

        `iterator` should be an iterator yielding pairs of (asset, ctable).
        """
        total_rows = 0
        first_row = {}
        last_row = {}
        calendar_offset = {}

        # Maps column name -> output carray.
        columns = {
            k: carray(array([], dtype=uint32_dtype))
            for k in US_EQUITY_PRICING_BCOLZ_COLUMNS
        }

        earliest_date = None
        sessions = self._calendar.sessions_in_range(
            self._start_session, self._end_session
        )

        if assets is not None:
            iterator = _check_asset_ids(iterator, set(assets))

        for asset_id, table in iterator:
            nrows = len(table)
            for column_name in columns:
                if column_name == "id":
                    # We know what the content of this column is, so don't
                    # bother reading it.
                    columns["id"].append(
                        full((nrows,), asset_id, dtype="uint32"),
                    )
                    continue

                columns[column_name].append(table[column_name])

            if earliest_date is None:
                earliest_date = table["day"][0]
            else:
                earliest_date = min(earliest_date, table["day"][0])

            # Bcolz doesn't support ints as keys in `attrs`, so convert
            # assets to strings for use as attr keys.
            asset_key = str(asset_id)

            # Calculate the index into the array of the first and last row
            # for this asset. This allows us to efficiently load single
            # assets when querying the data back out of the table.
            first_row[asset_key] = total_rows
            last_row[asset_key] = total_rows + nrows - 1
            total_rows += nrows

            table_day_to_session = compose(
                partial(self._calendar.date_to_session, direction="next"),
                partial(Timestamp, unit="s"),
            )
            asset_first_day = table_day_to_session(table["day"][0])
            asset_last_day = table_day_to_session(table["day"][-1])

            asset_sessions = sessions[
                sessions.slice_indexer(asset_first_day, asset_last_day)
            ]
            assert len(table) == len(asset_sessions), (
                "Got {} rows for daily bars table with first day={}, last "
                "day={}, expected {} rows.\n"
                "Missing sessions: {}\n"
                "Extra sessions: {}".format(
                    len(table),
                    asset_first_day.date(),
                    asset_last_day.date(),
                    len(asset_sessions),
                    asset_sessions.difference(
                        to_datetime(np.array(table["day"]), unit="s")
                    ).tolist(),
                    to_datetime(
                        np.array(table["day"]),
                        unit="s",
                    )
                    .difference(asset_sessions)
                    .tolist(),
                )
            )

            # Calculate the number of trading days between the first date
            # in the stored data and the first date of **this** asset. This
            # offset used for output alignment by the reader.
            calendar_offset[asset_key] = sessions.get_loc(asset_first_day)

        # This writes the table to disk.
        full_table = ctable(
            columns=[columns[colname] for colname in US_EQUITY_PRICING_BCOLZ_COLUMNS],
            names=US_EQUITY_PRICING_BCOLZ_COLUMNS,
            rootdir=self._filename,
            mode="w",
        )

        full_table.attrs["first_trading_day"] = (
            earliest_date if earliest_date is not None else iNaT
        )

        full_table.attrs["first_row"] = first_row
        full_table.attrs["last_row"] = last_row
        full_table.attrs["calendar_offset"] = calendar_offset
        full_table.attrs["calendar_name"] = self._calendar.name
        full_table.attrs["start_session_ns"] = self._start_session.value
        full_table.attrs["end_session_ns"] = self._end_session.value
        full_table.flush()
        return full_table

    @expect_element(invalid_data_behavior={"warn", "raise", "ignore"})
    def to_ctable(self, raw_data, invalid_data_behavior):
        if isinstance(raw_data, ctable):
            # we already have a ctable so do nothing
            return raw_data

        winsorise_uint32(raw_data, invalid_data_behavior, "volume", *OHLC)
        processed = (raw_data[list(OHLC)] * 1000).round().astype("uint32")
        dates = raw_data.index.values.astype("datetime64[s]")
        check_uint32_safe(dates.max().view(np.int64), "day")
        processed["day"] = dates.astype("uint32")
        processed["volume"] = raw_data.volume.astype("uint32")
        return ctable.fromdataframe(processed)


class BcolzDailyBarReader(CurrencyAwareSessionBarReader):
    """
    Reader for raw pricing data written by BcolzDailyOHLCVWriter.

    Parameters
    ----------
    table : bcolz.ctable
        The ctable contaning the pricing data, with attrs corresponding to the
        Attributes list below.
    read_all_threshold : int
        The number of equities at which; below, the data is read by reading a
        slice from the carray per asset.  above, the data is read by pulling
        all of the data for all assets into memory and then indexing into that
        array for each day and asset pair.  Used to tune performance of reads
        when using a small or large number of equities.

    Attributes
    ----------
    The table with which this loader interacts contains the following
    attributes:

    first_row : dict
        Map from asset_id -> index of first row in the dataset with that id.
    last_row : dict
        Map from asset_id -> index of last row in the dataset with that id.
    calendar_offset : dict
        Map from asset_id -> calendar index of first row.
    start_session_ns: int
        Epoch ns of the first session used in this dataset.
    end_session_ns: int
        Epoch ns of the last session used in this dataset.
    calendar_name: str
        String identifier of trading calendar used (ie, "NYSE").

    We use first_row and last_row together to quickly find ranges of rows to
    load when reading an asset's data into memory.

    We use calendar_offset and calendar to orient loaded blocks within a
    range of queried dates.

    Notes
    ------
    A Bcolz CTable is comprised of Columns and Attributes.
    The table with which this loader interacts contains the following columns:

    ['open', 'high', 'low', 'close', 'volume', 'day', 'id'].

    The data in these columns is interpreted as follows:

    - Price columns ('open', 'high', 'low', 'close') are interpreted as 1000 *
      as-traded dollar value.
    - Volume is interpreted as as-traded volume.
    - Day is interpreted as seconds since midnight UTC, Jan 1, 1970.
    - Id is the asset id of the row.

    The data in each column is grouped by asset and then sorted by day within
    each asset block.

    The table is built to represent a long time range of data, e.g. ten years
    of equity data, so the lengths of each asset block is not equal to each
    other. The blocks are clipped to the known start and end date of each asset
    to cut down on the number of empty values that would need to be included to
    make a regular/cubic dataset.

    When read across the open, high, low, close, and volume with the same
    index should represent the same asset and day.

    See Also
    --------
    zipline.data.bcolz_daily_bars.BcolzDailyBarWriter
    """

    def __init__(self, table, read_all_threshold=3000):
        self._maybe_table_rootdir = table
        # Cache of fully read np.array for the carrays in the daily bar table.
        # raw_array does not use the same cache, but it could.
        # Need to test keeping the entire array in memory for the course of a
        # process first.
        self._spot_cols = {}
        self.PRICE_ADJUSTMENT_FACTOR = 0.001
        self._read_all_threshold = read_all_threshold

    @cached_property
    def _table(self):
        maybe_table_rootdir = self._maybe_table_rootdir
        if isinstance(maybe_table_rootdir, ctable):
            return maybe_table_rootdir
        return ctable(rootdir=maybe_table_rootdir, mode="r")

    @cached_property
    def sessions(self):
        if "calendar" in self._table.attrs.attrs:
            # backwards compatibility with old formats, will remove
            return DatetimeIndex(self._table.attrs["calendar"])
        else:
            cal = get_calendar(self._table.attrs["calendar_name"])
            start_session_ns = self._table.attrs["start_session_ns"]
            start_session = Timestamp(start_session_ns)

            end_session_ns = self._table.attrs["end_session_ns"]
            end_session = Timestamp(end_session_ns)

            sessions = cal.sessions_in_range(start_session, end_session)

            return sessions

    @cached_property
    def _first_rows(self):
        return {
            int(asset_id): start_index
            for asset_id, start_index in self._table.attrs["first_row"].items()
        }

    @cached_property
    def _last_rows(self):
        return {
            int(asset_id): end_index
            for asset_id, end_index in self._table.attrs["last_row"].items()
        }

    @cached_property
    def _calendar_offsets(self):
        return {
            int(id_): offset
            for id_, offset in self._table.attrs["calendar_offset"].items()
        }

    @cached_property
    def first_trading_day(self):
        try:
            return Timestamp(
                self._table.attrs["first_trading_day"],
                unit="s",
            )
        except KeyError:
            return None

    @cached_property
    def trading_calendar(self):
        if "calendar_name" in self._table.attrs.attrs:
            return get_calendar(self._table.attrs["calendar_name"])
        else:
            return None

    @property
    def last_available_dt(self):
        return self.sessions[-1]

    def _compute_slices(self, start_idx, end_idx, assets):
        """
        Compute the raw row indices to load for each asset on a query for the
        given dates after applying a shift.

        Parameters
        ----------
        start_idx : int
            Index of first date for which we want data.
        end_idx : int
            Index of last date for which we want data.
        assets : pandas.Int64Index
            Assets for which we want to compute row indices

        Returns
        -------
        A 3-tuple of (first_rows, last_rows, offsets):
        first_rows : np.array[intp]
            Array with length == len(assets) containing the index of the first
            row to load for each asset in `assets`.
        last_rows : np.array[intp]
            Array with length == len(assets) containing the index of the last
            row to load for each asset in `assets`.
        offset : np.array[intp]
            Array with length == (len(asset) containing the index in a buffer
            of length `dates` corresponding to the first row of each asset.

            The value of offset[i] will be 0 if asset[i] existed at the start
            of a query.  Otherwise, offset[i] will be equal to the number of
            entries in `dates` for which the asset did not yet exist.
        """
        # The core implementation of the logic here is implemented in Cython
        # for efficiency.
        return _compute_row_slices(
            self._first_rows,
            self._last_rows,
            self._calendar_offsets,
            start_idx,
            end_idx,
            assets,
        )

    def load_raw_arrays(self, columns, start_date, end_date, assets):
        start_idx = self._load_raw_arrays_date_to_index(start_date)
        end_idx = self._load_raw_arrays_date_to_index(end_date)

        first_rows, last_rows, offsets = self._compute_slices(
            start_idx,
            end_idx,
            assets,
        )
        read_all = len(assets) > self._read_all_threshold
        return _read_bcolz_data(
            self._table,
            (end_idx - start_idx + 1, len(assets)),
            list(columns),
            first_rows,
            last_rows,
            offsets,
            read_all,
        )

    def _load_raw_arrays_date_to_index(self, date):
        try:
            return self.sessions.get_loc(date)
        except KeyError:
            raise NoDataOnDate(date) from None

    def _spot_col(self, colname):
        """
        Get the colname from daily_bar_table and read all of it into memory,
        caching the result.

        Parameters
        ----------
        colname : string
            A name of a OHLCV carray in the daily_bar_table

        Returns
        -------
        array (uint32)
            Full read array of the carray in the daily_bar_table with the
            given colname.
        """
        try:
            col = self._spot_cols[colname]
        except KeyError:
            col = self._spot_cols[colname] = self._table[colname]
        return col

    def get_last_traded_dt(self, asset, dt):
        volumes = self._spot_col("volume")

        search_day = dt

        while True:
            try:
                ix = self.sid_day_index(asset, search_day)
            except NoDataBeforeDate:
                return NaT
            except NoDataAfterDate:
                prev_day_ix = self.sessions.get_loc(search_day) - 1
                if prev_day_ix > -1:
                    search_day = self.sessions[prev_day_ix]
                continue
            except NoDataOnDate:
                return NaT
            if volumes[ix] != 0:
                return search_day
            prev_day_ix = self.sessions.get_loc(search_day) - 1
            if prev_day_ix > -1:
                search_day = self.sessions[prev_day_ix]
            else:
                return NaT

    def sid_day_index(self, sid, day):
        """
        Parameters
        ----------
        sid : int
            The asset identifier.
        day : datetime64-like
            Midnight of the day for which data is requested.

        Returns
        -------
        int
            Index into the data tape for the given sid and day.
            Raises a NoDataOnDate exception if the given day and sid is before
            or after the date range of the equity.
        """
        try:
            day_loc = self.sessions.get_loc(day)
        except Exception as err:
            raise NoDataOnDate(
                f"day={day} is outside of calendar={self.sessions}"
            ) from err
        offset = day_loc - self._calendar_offsets[sid]
        if offset < 0:
            raise NoDataBeforeDate(f"No data on or before day={day} for sid={sid}")
        ix = self._first_rows[sid] + offset
        if ix > self._last_rows[sid]:
            raise NoDataAfterDate(f"No data on or after day={day} for sid={sid}")
        return ix

    def get_value(self, sid, dt, field):
        """
        Parameters
        ----------
        sid : int
            The asset identifier.
        day : datetime64-like
            Midnight of the day for which data is requested.
        colname : string
            The price field. e.g. ('open', 'high', 'low', 'close', 'volume')

        Returns
        -------
        float
            The spot price for colname of the given sid on the given day.
            Raises a NoDataOnDate exception if the given day and sid is before
            or after the date range of the equity.
            Returns -1 if the day is within the date range, but the price is
            0.
        """
        ix = self.sid_day_index(sid, dt)
        price = self._spot_col(field)[ix]
        if field != "volume":
            if price == 0:
                return nan
            else:
                return price * 0.001
        else:
            return price

    def currency_codes(self, sids):
        # XXX: This is pretty inefficient. This reader doesn't really support
        # country codes, so we always either return USD or None if we don't
        # know about the sid at all.
        first_rows = self._first_rows
        out = []
        for sid in sids:
            if sid in first_rows:
                out.append("USD")
            else:
                out.append(None)
        return np.array(out, dtype=object)


def _compute_row_slices(
    asset_starts_absolute,
    asset_ends_absolute,
    asset_starts_calendar,
    query_start,
    query_end,
    requested_assets,
):
    """
    Core indexing functionality for loading raw data from bcolz.

    For each asset in requested assets, computes three values:

    1.) The index in the raw bcolz data of first row to load.
    2.) The index in the raw bcolz data of the last row to load.
    3.) The index in the dates of our query corresponding to the first row for
        each asset. This is non-zero iff the asset's lifetime begins partway
        through the requested query dates.

    Values for unknown sids will be populated with a value of -1.

    Parameters
    ----------
    asset_starts_absolute : dict
        Dictionary containing the index of the first row of each asset in the
        bcolz file from which we will query.
    asset_ends_absolute : dict
        Dictionary containing the index of the last row of each asset in the
        bcolz file from which we will query.
    asset_starts_calendar : dict
        Dictionary containing the index of in our calendar corresponding to the
        start date of each asset
    query_start : int
        Start index in our calendar of the dates for which we're querying.
    query_end : int
        End index in our calendar of the dates for which we're querying.
    requested_assets : pandas.Index[int64]
        The assets for which we want to load data.

    Returns
    -------
    first_rows, last_rows, offsets : 3-tuple of ndarrays
    """
    nassets = len(requested_assets)
    first_rows = np.full(nassets, -1, dtype=np.intp)
    last_rows = np.full(nassets, -1, dtype=np.intp)
    offsets = np.full(nassets, -1, dtype=np.intp)

    any_hits = False
    for i, asset in enumerate(requested_assets):
        if asset not in asset_starts_absolute:
            # This is an unknown asset, leave its slot empty.
            continue
        any_hits = True

        start_data = asset_starts_absolute[asset]
        end_data = asset_ends_absolute[asset]
        start_calendar = asset_starts_calendar[asset]
        end_calendar = start_calendar + (end_data - start_data)

        # The asset's first row, plus the rows before the query on which it
        # existed.
        first_rows[i] = start_data + max(0, query_start - start_calendar)
        # The asset's last row, minus the rows after the query on which it
        # existed.
        last_rows[i] = end_data - max(0, end_calendar - query_end)
        # The rows of the query before the asset existed.
        offsets[i] = max(0, start_calendar - query_start)

    if not any_hits:
        raise ValueError("At least one valid asset id is required.")

    return first_rows, last_rows, offsets


def _read_bcolz_data(table, shape, columns, first_rows, last_rows, offsets, read_all):
    """
    Load raw bcolz data for the given columns and indices.

    Parameters
    ----------
    table : bcolz.ctable
        The table from which to read.
    shape : tuple (length 2)
        The shape of the expected output arrays.
    columns : list[str]
        List of column names to read.
    first_rows : ndarray[intp]
    last_rows : ndarray[intp]
    offsets : ndarray[intp]
        Arrays in the format returned by _compute_row_slices.
    read_all : bool
        Whether to read all sids' data at once, or to read a slice from the
        carray for each sid.

    Returns
    -------
    results : list of ndarray
        A 2D array of shape `shape` for each column in `columns`.
    """
    nassets = shape[1]
    if not nassets == len(first_rows) == len(last_rows) == len(offsets):
        raise ValueError("Incompatible index arrays.")

    results = []
    for column in columns:
        out = np.zeros(shape, dtype=np.uint32)
        data = table[column][:] if read_all else table[column]
        for asset, (first, last, offset) in enumerate(
            zip(first_rows, last_rows, offsets, strict=True)
        ):
            # Unknown assets (-1) and empty slices leave their slots empty.
            if first == -1 or first > last:
                continue
            out[offset : offset + (last + 1 - first), asset] = data[first : last + 1]

        if column in OHLC:
            # Prices are stored as thousandths, and 0 for missing.
            prices = out.astype(np.float64) * 0.001
            prices[out == 0] = np.nan
            results.append(prices)
        else:
            results.append(out)
    return results
