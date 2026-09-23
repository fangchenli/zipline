from types import SimpleNamespace

import numpy as np
import pandas as pd

from zipline.data.in_memory_daily_bars import InMemoryDailyBarReader
from zipline.testing.fixtures import WithTradingCalendars, ZiplineTestCase
from zipline.testing.predicates import assert_equal


class InMemoryDailyBarReaderTestCase(WithTradingCalendars, ZiplineTestCase):
    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.sessions = self.trading_calendar.sessions_in_range(
            pd.Timestamp("2020-01-02"),
            pd.Timestamp("2020-01-08"),
        )
        # sid 1 has no close on the 3rd session, then trades again.
        close = pd.DataFrame(
            {1: [10.0, 11.0, np.nan, 13.0, 14.0]},
            index=self.sessions,
        )
        frames = {field: close for field in ("open", "high", "low", "close", "volume")}
        self.reader = InMemoryDailyBarReader(
            frames,
            self.trading_calendar,
            currency_codes=pd.Series(index=[1], data="USD"),
        )

    def test_get_last_traded_dt_does_not_look_ahead(self):
        asset = SimpleNamespace(sid=1)
        # On the missing session, the last trade is the session before it,
        # not the last trade in the whole frame.
        assert_equal(
            self.reader.get_last_traded_dt(asset, self.sessions[2]),
            self.sessions[1],
        )
        assert_equal(
            self.reader.get_last_traded_dt(asset, self.sessions[-1]),
            self.sessions[-1],
        )

    def test_get_last_traded_dt_missing(self):
        # Before any data, and for an unknown sid, there is no last trade.
        assert (
            self.reader.get_last_traded_dt(
                SimpleNamespace(sid=1),
                pd.Timestamp("2019-12-31"),
            )
            is pd.NaT
        )
        assert (
            self.reader.get_last_traded_dt(
                SimpleNamespace(sid=2),
                self.sessions[-1],
            )
            is pd.NaT
        )
