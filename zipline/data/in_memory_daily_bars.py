import numpy as np
import pandas as pd
from pandas import NaT

from zipline.data.bar_reader import OHLCV, NoDataForSid, NoDataOnDate
from zipline.data.session_bars import CurrencyAwareSessionBarReader
from zipline.utils.calendar_utils import ExchangeCalendar
from zipline.utils.input_validation import expect_types, validate_keys
from zipline.utils.pandas_utils import check_indexes_all_same


class InMemoryDailyBarReader(CurrencyAwareSessionBarReader):
    """
    A SessionBarReader backed by a dictionary of in-memory DataFrames.

    Parameters
    ----------
    frames : dict[str -> pd.DataFrame]
        Dictionary from field name ("open", "high", "low", "close", or
        "volume") to DataFrame containing data for that field.
    calendar : str or zipline.utils.calendar_utils.ExchangeCalendar
        Calendar (or name of calendar) to which data is aligned.
    currency_codes : pd.Series
        Map from sid -> listing currency for that sid.
    verify_indices : bool, optional
        Whether or not to verify that input data is correctly aligned to the
        given calendar. Default is True.
    """

    @expect_types(
        frames=dict,
        calendar=ExchangeCalendar,
        verify_indices=bool,
        currency_codes=pd.Series,
    )
    def __init__(self, frames, calendar, currency_codes, verify_indices=True):
        self._frames = frames
        self._values = {key: frame.values for key, frame in frames.items()}
        self._calendar = calendar
        self._currency_codes = currency_codes

        validate_keys(frames, set(OHLCV), type(self).__name__)
        if verify_indices:
            verify_frames_aligned(list(frames.values()), calendar)

        self._sessions = frames["close"].index
        self._sids = frames["close"].columns

    @property
    def last_available_dt(self):
        return self._sessions[-1]

    @property
    def trading_calendar(self):
        return self._calendar

    @property
    def sessions(self):
        return self._sessions

    def load_raw_arrays(self, columns, start_date, end_date, assets):
        if start_date not in self._sessions:
            raise NoDataOnDate(start_date)
        if end_date not in self._sessions:
            raise NoDataOnDate(end_date)

        asset_indexer = self._sids.get_indexer(assets)
        if -1 in asset_indexer:
            bad_assets = assets[asset_indexer == -1]
            raise NoDataForSid(bad_assets)

        date_indexer = self._sessions.slice_indexer(start_date, end_date)

        out = []
        for c in columns:
            out.append(self._values[c][date_indexer, asset_indexer])

        return out

    def get_value(self, sid, dt, field):
        """
        Parameters
        ----------
        sid : int
            The asset identifier.
        day : datetime64-like
            Midnight of the day for which data is requested.
        field : string
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
        return self._frames[field].loc[dt, sid]

    def get_last_traded_dt(self, asset, dt):
        """
        Parameters
        ----------
        asset : zipline.asset.Asset
            The asset identifier.
        dt : datetime64-like
            Midnight of the day for which data is requested.

        Returns
        -------
        pd.Timestamp : The last know dt for the asset and dt;
                       NaT if no trade is found before the given dt.
        """
        try:
            closes = self._frames["close"].loc[:dt, asset.sid]
        except KeyError:
            return NaT
        last = closes.last_valid_index()
        return NaT if last is None else last

    @property
    def first_trading_day(self):
        return self._sessions[0]

    def currency_codes(self, sids):
        codes = self._currency_codes
        return np.array([codes[sid] for sid in sids])


def verify_frames_aligned(frames, calendar):
    """
    Verify that DataFrames in ``frames`` have the same indexing scheme and are
    aligned to ``calendar``.

    Parameters
    ----------
    frames : list[pd.DataFrame]
    calendar : zipline.utils.calendar_utils.ExchangeCalendar

    Raises
    ------
    ValueError
        If frames have different indexes/columns, or if frame indexes do not
        match a contiguous region of ``calendar``.
    """
    indexes = [f.index for f in frames]
    check_indexes_all_same(indexes, message="DataFrame indexes don't match:")

    columns = [f.columns for f in frames]
    check_indexes_all_same(columns, message="DataFrame columns don't match:")

    start, end = indexes[0][[0, -1]]
    cal_sessions = calendar.sessions_in_range(start, end)

    check_indexes_all_same(
        [indexes[0], cal_sessions],
        f"DataFrame index doesn't match {calendar.name} calendar:",
    )
