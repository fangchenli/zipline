import json
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from zipline.data.bar_reader import NoDataForSid, NoDataOnDate
from zipline.data.parquet_minute_bars import (
    FORMAT_VERSION,
    ParquetMinuteBarReader,
    ParquetMinuteBarWriter,
)
from zipline.testing.fixtures import (
    WithBcolzEquityMinuteBarReader,
    WithInstanceTmpDir,
    WithParquetEquityMinuteBarReader,
    WithTradingCalendars,
    ZiplineTestCase,
)
from zipline.testing.predicates import assert_equal

FIELDS = ["open", "high", "low", "close", "volume"]


class ParquetMinuteBarReaderTestCase(
    WithParquetEquityMinuteBarReader, WithBcolzEquityMinuteBarReader, ZiplineTestCase
):
    """Compare the Parquet reader with the bcolz reader on the same bars.

    The range includes month boundaries and the early closes after
    Thanksgiving and on Christmas Eve 2015.
    """

    START_DATE = pd.Timestamp("2015-11-20")
    END_DATE = pd.Timestamp("2016-01-08")
    ASSET_FINDER_EQUITY_SIDS = 1, 2, 5, 8

    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.reader = self.parquet_equity_minute_bar_reader
        self.bcolz = self.bcolz_equity_minute_bar_reader
        self.minutes = self.trading_calendar.sessions_minutes(
            self.START_DATE, self.END_DATE
        )
        self.sids = list(self.ASSET_FINDER_EQUITY_SIDS)

    def assert_matches_bcolz(self, reader, start, end, assets):
        sids = [int(asset) for asset in assets]
        expected = self.bcolz.load_raw_arrays(FIELDS, start, end, sids)
        actual = reader.load_raw_arrays(FIELDS, start, end, assets)
        for field, e, a in zip(FIELDS, expected, actual):
            assert_equal(a, e.astype("float64"), msg=field)

    def test_metadata(self):
        reader = self.reader
        assert_equal(reader.first_trading_day, self.bcolz.first_trading_day)
        assert_equal(reader.last_available_dt, self.bcolz.last_available_dt)
        assert_equal(reader.trading_calendar.name, self.bcolz.trading_calendar.name)
        assert_equal(reader.data_frequency, "minute")

    def test_load_raw_arrays(self):
        minutes = self.minutes
        # Thanksgiving's early close falls on 2015-11-27.
        early_close = minutes.get_loc(pd.Timestamp("2015-11-27 18:00", tz="UTC"))
        windows = [
            (minutes[0], minutes[-1]),
            (minutes[early_close - 30], minutes[early_close + 30]),
            (pd.Timestamp("2015-11-30 20:00", tz="UTC"), minutes[-1000]),
            (minutes[500], minutes[500]),
        ]
        for start, end in windows:
            self.assert_matches_bcolz(self.reader, start, end, self.sids)

    def test_accepts_assets(self):
        assets = self.asset_finder.retrieve_all(self.sids)
        self.assert_matches_bcolz(
            self.reader, self.minutes[0], self.minutes[-1], assets
        )
        asset = assets[0]
        minute = self.minutes[1234]
        assert_equal(
            self.reader.get_value(asset, minute, "close"),
            self.bcolz.get_value(asset.sid, minute, "close"),
        )

    def test_small_block_cache(self):
        reader = ParquetMinuteBarReader(
            self.parquet_equity_minute_bar_path, block_cache_bytes=1
        )
        self.assert_matches_bcolz(reader, self.minutes[0], self.minutes[-1], self.sids)
        self.assert_matches_bcolz(reader, self.minutes[100], self.minutes[9000], [5, 1])
        assert_equal(len(reader._blocks), 1)

    def test_get_value(self):
        rng = np.random.default_rng(0)
        for i in rng.integers(0, len(self.minutes), 200):
            for sid in self.sids:
                for field in FIELDS:
                    assert_equal(
                        self.reader.get_value(sid, self.minutes[i], field),
                        self.bcolz.get_value(sid, self.minutes[i], field),
                        msg=f"{sid} {self.minutes[i]} {field}",
                    )

    def test_get_value_non_trading_minute(self):
        # After Thanksgiving's early close.
        with pytest.raises(NoDataOnDate):
            self.reader.get_value(
                1, pd.Timestamp("2015-11-27 19:00", tz="UTC"), "close"
            )
        with pytest.raises(NoDataOnDate):
            self.reader.get_value(
                1, pd.Timestamp("2016-01-09 15:00", tz="UTC"), "close"
            )

    def test_get_last_traded_dt(self):
        asset = self.asset_finder.retrieve_asset(2)
        for dt in [
            self.minutes[0],
            self.minutes[4321],
            self.minutes[-1],
            # After an early close and overnight: the last traded minute is
            # the session's last minute.
            pd.Timestamp("2015-11-27 20:00", tz="UTC"),
            pd.Timestamp("2015-12-01 03:00", tz="UTC"),
        ]:
            # bcolz labels minutes with datetime.timezone.utc rather than the
            # calendar's ZoneInfo("UTC"), so compare the instants.
            actual = self.reader.get_last_traded_dt(asset, dt)
            assert actual == self.bcolz.get_last_traded_dt(asset, dt), dt
        before = pd.Timestamp("2015-11-20 14:00", tz="UTC")
        assert self.reader.get_last_traded_dt(asset, before) is pd.NaT

    def test_unknown_sid(self):
        minute = self.minutes[0]
        with pytest.raises(NoDataForSid):
            self.reader.get_value(1000, minute, "close")
        with pytest.raises(NoDataForSid):
            self.reader.load_raw_arrays(["close"], minute, minute, [1, 1000])
        assert self.reader.get_last_traded_dt(1000, minute) is pd.NaT


class ParquetMinuteBarBreaksTestCase(
    WithTradingCalendars, WithInstanceTmpDir, ZiplineTestCase
):
    """Hong Kong's lunch break."""

    TRADING_CALENDAR_STRS = ("XHKG",)
    TRADING_CALENDAR_PRIMARY_CAL = "XHKG"
    START_DATE = pd.Timestamp("2016-01-04")
    END_DATE = pd.Timestamp("2016-01-08")

    def test_window_starting_in_break(self):
        calendar = self.trading_calendar
        minutes = calendar.sessions_minutes(self.START_DATE, self.END_DATE)
        path = self.instance_tmpdir.getpath("minute_bars")
        frame = ParquetMinuteBarWriterTestCase.frame(minutes)
        ParquetMinuteBarWriter(path, calendar, self.START_DATE, self.END_DATE).write(
            [(1, frame)]
        )
        reader = ParquetMinuteBarReader(path)

        # 12:00 is the last minute before the break and 13:01 the first after
        # it, 04:00 and 05:01 UTC. The daily history aggregator asks for the
        # window after the last minute it saw, which starts in the break.
        before = pd.Timestamp("2016-01-05 04:00", tz="UTC")
        after = pd.Timestamp("2016-01-05 05:01", tz="UTC")
        in_break = before + pd.Timedelta(minutes=1)
        (close,) = reader.load_raw_arrays(["close"], in_break, after, [1])
        assert_equal(close, np.array([[frame.loc[after, "close"]]]))

        (close,) = reader.load_raw_arrays(["close"], before, after, [1])
        assert_equal(close[:, 0], frame.loc[[before, after], "close"].to_numpy())

        (close,) = reader.load_raw_arrays(
            ["close"], in_break, in_break + pd.Timedelta(minutes=5), [1]
        )
        assert_equal(close.shape, (0, 1))
        with pytest.raises(NoDataOnDate):
            reader.get_value(1, in_break, "close")


class ParquetMinuteBarWriterTestCase(
    WithTradingCalendars, WithInstanceTmpDir, ZiplineTestCase
):
    START_DATE = pd.Timestamp("2015-11-25")
    END_DATE = pd.Timestamp("2015-12-02")

    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.path = self.instance_tmpdir.getpath("minute_bars")
        self.minutes = self.trading_calendar.sessions_minutes(
            self.START_DATE, self.END_DATE
        )

    def writer(self, **kwargs):
        return ParquetMinuteBarWriter(
            self.path, self.trading_calendar, self.START_DATE, self.END_DATE, **kwargs
        )

    def write(self, data, **kwargs):
        writer_kwargs = {
            key: kwargs.pop(key)
            for key in ("row_group_size", "max_buffered_rows")
            if key in kwargs
        }
        self.writer(**writer_kwargs).write(data, **kwargs)
        return ParquetMinuteBarReader(self.path)

    @staticmethod
    def frame(minutes, start=10.0, volume=100):
        n = len(minutes)
        return pd.DataFrame(
            {
                "open": start + np.arange(n),
                "high": start + 1 + np.arange(n),
                "low": start - 1 + np.arange(n),
                "close": start + np.arange(n),
                "volume": np.full(n, volume, dtype="int64"),
            },
            index=minutes,
        )

    def test_sparse_bars(self):
        traded = self.minutes[[3, 10, 11, 700]]
        reader = self.write([(1, self.frame(traded))])
        (close, volume) = reader.load_raw_arrays(
            ["close", "volume"], self.minutes[0], self.minutes[-1], [1]
        )
        expected_close = np.full(len(self.minutes), np.nan)
        expected_close[[3, 10, 11, 700]] = [10.0, 11.0, 12.0, 13.0]
        assert_equal(close[:, 0], expected_close)
        assert_equal(np.flatnonzero(volume[:, 0]), np.array([3, 10, 11, 700]))

        assert_equal(reader.get_value(1, self.minutes[10], "close"), 11.0)
        assert np.isnan(reader.get_value(1, self.minutes[12], "close"))
        assert_equal(reader.get_value(1, self.minutes[12], "volume"), 0.0)
        assert_equal(reader.get_last_traded_dt(1, self.minutes[699]), self.minutes[11])

    def test_price_without_volume_is_not_a_trade(self):
        frame = self.frame(self.minutes[:3])
        frame.loc[self.minutes[2], "volume"] = 0
        reader = self.write([(1, frame)])
        assert_equal(reader.get_value(1, self.minutes[2], "close"), 12.0)
        assert_equal(reader.get_last_traded_dt(1, self.minutes[2]), self.minutes[1])

    def test_zero_price_is_missing(self):
        frame = self.frame(self.minutes[:2])
        frame.loc[self.minutes[0], "open"] = 0.0
        frame.loc[self.minutes[1], "volume"] = np.nan
        reader = self.write([(1, frame)])
        assert np.isnan(reader.get_value(1, self.minutes[0], "open"))
        assert_equal(reader.get_value(1, self.minutes[0], "close"), 10.0)
        assert_equal(reader.get_value(1, self.minutes[1], "volume"), 0.0)

    def test_volume_beyond_uint32(self):
        reader = self.write([(1, self.frame(self.minutes[:1], volume=2**40))])
        assert_equal(reader.get_value(1, self.minutes[0], "volume"), float(2**40))

    def test_invalid_data(self):
        frame = self.frame(self.minutes[:2])
        frame.loc[self.minutes[0], "high"] = -1.0
        frame.loc[self.minutes[1], "low"] = np.inf
        with pytest.raises(ValueError, match="2 negative or infinite values"):
            self.write([(1, frame)], invalid_data_behavior="raise")
        with pytest.warns(UserWarning, match="2 negative or infinite values"):
            reader = self.write([(1, frame)], invalid_data_behavior="warn")
        assert np.isnan(reader.get_value(1, self.minutes[0], "high"))
        assert np.isnan(reader.get_value(1, self.minutes[1], "low"))

    def test_not_trading_minutes(self):
        # 19:00 UTC on 2015-11-27 is after the early close.
        minutes = pd.DatetimeIndex(
            [self.minutes[0], pd.Timestamp("2015-11-27 19:00", tz="UTC")]
        )
        with pytest.raises(ValueError, match="1 minutes are not trading minutes"):
            self.write([(1, self.frame(minutes))])

    def test_naive_minutes_are_utc(self):
        reader = self.write([(1, self.frame(self.minutes[:5].tz_localize(None)))])
        assert_equal(reader.get_value(1, self.minutes[4], "close"), 14.0)

    def test_unsorted_input(self):
        """Out-of-order sids and minutes, in many small row groups."""
        order = np.random.default_rng(0).permutation(len(self.minutes))
        data = [
            (sid, self.frame(self.minutes, start=10.0 * sid).iloc[order])
            for sid in (7, 3, 5)
        ]
        reader = self.write(data, row_group_size=100, max_buffered_rows=500)
        (close,) = reader.load_raw_arrays(
            ["close"], self.minutes[0], self.minutes[-1], [3, 5, 7]
        )
        for i, sid in enumerate([3, 5, 7]):
            assert_equal(close[:, i], 10.0 * sid + np.arange(len(self.minutes)))
        month_file = pq.ParquetFile(f"{self.path}/year=2015/month=11/data.parquet")
        assert month_file.metadata.num_row_groups > 1

    def test_asset_without_bars(self):
        reader = self.write(
            [(1, self.frame(self.minutes[:0])), (2, self.frame(self.minutes[:1]))]
        )
        assert np.isnan(reader.get_value(1, self.minutes[0], "close"))
        assert reader.get_last_traded_dt(1, self.minutes[-1]) is pd.NaT
        (close,) = reader.load_raw_arrays(
            ["close"], self.minutes[0], self.minutes[0], [1, 2]
        )
        assert_equal(close, np.array([[np.nan, 10.0]]))

    def test_duplicate_sid(self):
        frame = self.frame(self.minutes[:1])
        with pytest.raises(ValueError, match="sid 1 appears more than once"):
            self.write([(1, frame), (1, frame)])

    def test_refuses_to_overwrite(self):
        self.write([(1, self.frame(self.minutes[:1]))])
        with pytest.raises(ValueError, match="is not empty"):
            self.write([(1, self.frame(self.minutes[:1]))])

    def test_newer_format_version(self):
        self.write([(1, self.frame(self.minutes[:1]))])
        with open(f"{self.path}/_metadata.json") as f:
            metadata = json.load(f)
        metadata["version"] = FORMAT_VERSION + 1
        with open(f"{self.path}/_metadata.json", "w") as f:
            json.dump(metadata, f)
        reader = ParquetMinuteBarReader(self.path)
        with pytest.raises(ValueError, match="format version"):
            reader.get_value(1, self.minutes[0], "close")

    def test_opens_lazily(self):
        reader = ParquetMinuteBarReader(self.path)
        self.write([(1, self.frame(self.minutes[:1]))])
        assert_equal(reader.get_value(1, self.minutes[0], "close"), 10.0)

    def test_failed_write_leaves_rootdir_unusable(self):
        """A retry can't mix its months with a failed write's leftovers."""
        good = self.frame(self.minutes[:5])
        bad = self.frame(self.minutes[-5:])
        bad.loc[self.minutes[-1], "close"] = -1.0
        with pytest.raises(ValueError):
            self.write(
                [(1, good), (2, bad)], row_group_size=1, invalid_data_behavior="raise"
            )
        with pytest.raises(ValueError, match="is not empty"):
            self.write([(2, self.frame(self.minutes[-5:]))])

    def test_missing_metadata(self):
        self.write([(1, self.frame(self.minutes[:1]))])
        os.rename(f"{self.path}/_metadata.json", f"{self.path}/metadata.json")
        reader = ParquetMinuteBarReader(self.path)
        with pytest.raises(ValueError, match="_metadata.json is missing"):
            reader.get_value(1, self.minutes[0], "close")

    def test_different_calendar(self):
        """Bars at minutes the reader's calendar lacks raise a clear error."""
        # Toronto is closed on Canada Day, when New York trades.
        start, end = pd.Timestamp("2015-06-29"), pd.Timestamp("2015-07-02")
        minutes = self.trading_calendar.sessions_minutes(start, end)
        ParquetMinuteBarWriter(self.path, self.trading_calendar, start, end).write(
            [(1, self.frame(minutes))]
        )
        with open(f"{self.path}/_metadata.json") as f:
            metadata = json.load(f)
        metadata["calendar_name"] = "XTSE"
        with open(f"{self.path}/_metadata.json", "w") as f:
            json.dump(metadata, f)
        reader = ParquetMinuteBarReader(self.path)
        # July's row group holds the Canada Day bars.
        with pytest.raises(ValueError, match="not trading minutes"):
            reader.get_value(1, minutes[-1], "close")

    def test_small_cache_bounds_reads(self):
        """Large reads stream through a small cache and stay correct."""
        sids = list(range(1, 21))
        self.write(
            [(sid, self.frame(self.minutes, start=10.0 * sid)) for sid in sids],
            row_group_size=len(self.minutes),
        )
        # Room for about two sids' bars per month.
        budget = 2 * len(self.minutes) * 44
        reader = ParquetMinuteBarReader(self.path, block_cache_bytes=budget)
        (close,) = reader.load_raw_arrays(
            ["close"], self.minutes[0], self.minutes[-1], sids
        )
        for i, sid in enumerate(sids):
            assert_equal(close[:, i], 10.0 * sid + np.arange(len(self.minutes)))
        assert reader._cached_bytes <= budget

        for sid in sids:
            assert_equal(
                reader.get_value(sid, self.minutes[7], "close"), 10.0 * sid + 7
            )
        assert reader._cached_bytes <= budget

    def test_readable_as_dataset(self):
        self.write(
            [(1, self.frame(self.minutes[:3])), (2, self.frame(self.minutes[-2:]))]
        )
        frame = pd.read_parquet(self.path, columns=["sid", "dt", "close"])
        assert_equal(len(frame), 5)
        assert_equal(sorted(frame["sid"].unique()), [1, 2])
