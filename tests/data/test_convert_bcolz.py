import os

import numpy as np
import pandas as pd

from zipline.data.bcolz_daily_bars import BcolzDailyBarReader, BcolzDailyBarWriter
from zipline.data.convert_bcolz import convert_daily_bars, convert_minute_bars
from zipline.data.minute_bars import (
    US_EQUITIES_MINUTES_PER_DAY,
    BcolzMinuteBarReader,
    BcolzMinuteBarWriter,
)
from zipline.data.parquet_daily_bars import ParquetDailyBarReader
from zipline.data.parquet_minute_bars import ParquetMinuteBarReader
from zipline.testing.fixtures import (
    WithInstanceTmpDir,
    WithTradingCalendars,
    ZiplineTestCase,
)
from zipline.testing.predicates import assert_equal

FIELDS = ["open", "high", "low", "close", "volume"]


def bars(index, seed, volume=1000):
    """Random bars with prices that bcolz rounds to three decimals."""
    rng = np.random.default_rng(seed)
    close = 50 + np.cumsum(rng.normal(0, 0.1234567, len(index)))
    return pd.DataFrame(
        {
            "open": close + 0.01,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(len(index), volume, dtype="float64"),
        },
        index=index,
    )


class ConvertBcolzTestCase(WithTradingCalendars, WithInstanceTmpDir, ZiplineTestCase):
    # Includes month boundaries and the early closes after Thanksgiving and
    # on Christmas Eve.
    START_DATE = pd.Timestamp("2015-11-20")
    END_DATE = pd.Timestamp("2016-01-08")

    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.sessions = self.trading_calendar.sessions_in_range(
            self.START_DATE, self.END_DATE
        )
        self.minutes = self.trading_calendar.sessions_minutes(
            self.START_DATE, self.END_DATE
        )

    def path(self, name):
        return self.instance_tmpdir.getpath(name)

    def test_daily(self):
        sessions = self.sessions
        data = {
            # The whole range.
            1: bars(sessions, 1),
            # Starts late and ends early, with a missing day.
            2: bars(sessions[5:30], 2),
            # Starts on the last session.
            3: bars(sessions[-1:], 3),
        }
        data[2].iloc[10] = np.nan
        BcolzDailyBarWriter(
            self.path("bcolz"), self.trading_calendar, sessions[0], sessions[-1]
        ).write(data.items(), invalid_data_behavior="ignore")

        convert_daily_bars(self.path("bcolz"), self.path("parquet"))

        bcolz = BcolzDailyBarReader(self.path("bcolz"))
        parquet = ParquetDailyBarReader(self.path("parquet"))
        sids = list(data)
        assert_equal(parquet.sessions, bcolz.sessions)
        assert_equal(parquet.first_trading_day, bcolz.first_trading_day)
        assert_equal(parquet.trading_calendar.name, bcolz.trading_calendar.name)
        assert_equal(
            list(parquet.currency_codes(sids)), list(bcolz.currency_codes(sids))
        )
        expected = bcolz.load_raw_arrays(FIELDS, sessions[0], sessions[-1], sids)
        actual = parquet.load_raw_arrays(FIELDS, sessions[0], sessions[-1], sids)
        for field, e, a in zip(FIELDS, expected, actual):
            assert_equal(a, e.astype("float64"), msg=field)
        for sid, frame in data.items():
            # Lifetimes survive, including a missing first or last value.
            for dt in (frame.index[0], frame.index[-1]):
                assert_equal(
                    parquet.get_last_traded_dt(sid, dt),
                    bcolz.get_last_traded_dt(sid, dt),
                    msg=f"{sid} {dt}",
                )

    def test_minute(self):
        minutes = self.minutes
        data = {
            # Every minute.
            1: bars(minutes, 1),
            # Every seventh minute, starting in the second session.
            2: bars(minutes[400::7], 2),
            # A single bar with a price but no volume.
            3: bars(minutes[1000:1001], 3, volume=0),
        }
        os.makedirs(self.path("bcolz"))
        BcolzMinuteBarWriter(
            self.path("bcolz"),
            self.trading_calendar,
            self.sessions[0],
            self.sessions[-1],
            US_EQUITIES_MINUTES_PER_DAY,
        ).write(data.items())

        convert_minute_bars(self.path("bcolz"), self.path("parquet"))

        bcolz = BcolzMinuteBarReader(self.path("bcolz"))
        parquet = ParquetMinuteBarReader(self.path("parquet"))
        sids = list(data)
        assert_equal(parquet.first_trading_day, bcolz.first_trading_day)
        assert_equal(parquet.last_available_dt, bcolz.last_available_dt)
        expected = bcolz.load_raw_arrays(FIELDS, minutes[0], minutes[-1], sids)
        actual = parquet.load_raw_arrays(FIELDS, minutes[0], minutes[-1], sids)
        for field, e, a in zip(FIELDS, expected, actual):
            assert_equal(a, e.astype("float64"), msg=field)
        # Only minutes with a bar are stored.
        n_rows = sum(
            len(pd.read_parquet(os.path.join(self.path("parquet"), d)))
            for d in os.listdir(self.path("parquet"))
            if d.startswith("year=")
        )
        assert_equal(n_rows, len(minutes) + len(minutes[400::7]) + 1)

    def test_empty_minute_dataset(self):
        os.makedirs(self.path("bcolz"))
        BcolzMinuteBarWriter(
            self.path("bcolz"),
            self.trading_calendar,
            self.sessions[0],
            self.sessions[-1],
            US_EQUITIES_MINUTES_PER_DAY,
        )
        convert_minute_bars(self.path("bcolz"), self.path("parquet"))
        parquet = ParquetMinuteBarReader(self.path("parquet"))
        assert_equal(parquet.first_trading_day, self.sessions[0])
        assert_equal(parquet.last_available_dt, self.minutes[-1])
