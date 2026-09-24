#
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
import json
import re
from itertools import cycle, islice
from sys import maxsize

import numpy as np
import pytest
from numpy import (
    arange,
    nan,
)
from pandas import (
    DataFrame,
    DatetimeIndex,
    NaT,
    Series,
    Timestamp,
    concat,
    read_parquet,
)
from toolz import merge

from zipline.data.bar_reader import (
    NoDataAfterDate,
    NoDataBeforeDate,
    NoDataOnDate,
)
from zipline.data.bcolz_daily_bars import BcolzDailyBarWriter
from zipline.data.multi_country_daily_bars import MultiCountryDailyBarReader
from zipline.data.parquet_daily_bars import (
    FORMAT_VERSION,
    ParquetDailyBarReader,
    ParquetDailyBarWriter,
)
from zipline.pipeline.loaders.synthetic import (
    OHLCV,
    asset_end,
    asset_start,
    expected_bar_value_with_holes,
    expected_bar_values_2d,
    make_bar_data,
)
from zipline.testing import powerset
from zipline.testing.fixtures import (
    WithAssetFinder,
    WithBcolzEquityDailyBarReader,
    WithEquityDailyBarData,
    WithParquetEquityDailyBarReader,
    WithSeededRandomState,
    WithTmpDir,
    WithTradingCalendars,
    ZiplineTestCase,
)
from zipline.testing.predicates import assert_equal
from zipline.utils.calendar_utils import get_calendar
from zipline.utils.classproperty import classproperty

CLOSE = "close"
VOLUME = "volume"

TEST_CALENDAR_START = Timestamp("2015-06-01")
TEST_CALENDAR_STOP = Timestamp("2015-06-30")

TEST_QUERY_START = Timestamp("2015-06-10")
TEST_QUERY_STOP = Timestamp("2015-06-19")

# NOTE: All sids here are odd, so we can test querying for unknown sids
#       with evens.
us_info = DataFrame(
    [
        # 1) The equity's trades start and end before query.
        {"start_date": "2015-06-01", "end_date": "2015-06-05"},
        # 3) The equity's trades start and end after query.
        {"start_date": "2015-06-22", "end_date": "2015-06-30"},
        # 5) The equity's data covers all dates in range (but we define
        #    a hole for it on 2015-06-17).
        {"start_date": "2015-06-02", "end_date": "2015-06-30"},
        # 7) The equity's trades start before the query start, but stop
        #    before the query end.
        {"start_date": "2015-06-01", "end_date": "2015-06-15"},
        # 9) The equity's trades start and end during the query.
        {"start_date": "2015-06-12", "end_date": "2015-06-18"},
        # 11) The equity's trades start during the query, but extend through
        #    the whole query.
        {"start_date": "2015-06-15", "end_date": "2015-06-25"},
    ],
    index=arange(1, 12, step=2),
    columns=["start_date", "end_date"],
).astype("datetime64[ns]")
us_info["exchange"] = "NYSE"

ca_info = DataFrame(
    [
        # 13) The equity's trades start and end before query.
        {"start_date": "2015-06-01", "end_date": "2015-06-05"},
        # 15) The equity's trades start and end after query.
        {"start_date": "2015-06-22", "end_date": "2015-06-30"},
        # 17) The equity's data covers all dates in range.
        {"start_date": "2015-06-02", "end_date": "2015-06-30"},
        # 19) The equity's trades start before the query start, but stop
        #    before the query end.
        {"start_date": "2015-06-01", "end_date": "2015-06-15"},
        # 21) The equity's trades start and end during the query.
        {"start_date": "2015-06-12", "end_date": "2015-06-18"},
        # 23) The equity's trades start during the query, but extend through
        #    the whole query.
        {"start_date": "2015-06-15", "end_date": "2015-06-25"},
    ],
    index=arange(13, 24, step=2),
    columns=["start_date", "end_date"],
).astype("datetime64[ns]")
ca_info["exchange"] = "TSX"

EQUITY_INFO = concat([us_info, ca_info])
EQUITY_INFO["symbol"] = [chr(ord("A") + x) for x in range(len(EQUITY_INFO))]

TEST_QUERY_ASSETS = EQUITY_INFO.index
assert (TEST_QUERY_ASSETS % 2 == 1).all(), "All sids should be odd."

HOLES = {
    "US": {5: (Timestamp("2015-06-17"),)},
    "CA": {17: (Timestamp("2015-06-17"),)},
}


class _DailyBarsTestCase(
    WithEquityDailyBarData, WithSeededRandomState, ZiplineTestCase
):
    EQUITY_DAILY_BAR_START_DATE = TEST_CALENDAR_START
    EQUITY_DAILY_BAR_END_DATE = TEST_CALENDAR_STOP

    # The country under which these tests should be run.
    DAILY_BARS_TEST_QUERY_COUNTRY_CODE = "US"

    # Currencies to use for assets in these tests.
    DAILY_BARS_TEST_CURRENCIES = {"US": ["USD"], "CA": ["USD", "CAD"]}

    @classmethod
    def init_class_fixtures(cls):
        super().init_class_fixtures()

        cls.sessions = cls.trading_calendar.sessions_in_range(
            cls.trading_calendar.minute_to_session(TEST_CALENDAR_START),
            cls.trading_calendar.minute_to_session(TEST_CALENDAR_STOP),
        )

    @classmethod
    def make_equity_info(cls):
        return EQUITY_INFO

    @classmethod
    def make_exchanges_info(cls, *args, **kwargs):
        return DataFrame({"exchange": ["NYSE", "TSX"], "country_code": ["US", "CA"]})

    @classmethod
    def make_equity_daily_bar_data(cls, country_code, sids):
        # Create the data for all countries.
        return make_bar_data(
            EQUITY_INFO.loc[list(sids)],
            cls.equity_daily_bar_days,
            holes=merge(HOLES.values()),
        )

    @classmethod
    def make_equity_daily_bar_currency_codes(cls, country_code, sids):
        # Evenly distribute choices among ``sids``.
        choices = cls.DAILY_BARS_TEST_CURRENCIES[country_code]
        codes = list(islice(cycle(choices), len(sids)))
        return Series(index=sids, data=np.array(codes, dtype=object))

    @classproperty
    def holes(cls):
        return HOLES[cls.DAILY_BARS_TEST_QUERY_COUNTRY_CODE]

    @property
    def assets(self):
        return list(
            self.asset_finder.equities_sids_for_country_code(
                self.DAILY_BARS_TEST_QUERY_COUNTRY_CODE
            )
        )

    def trading_days_between(self, start, end):
        return self.sessions[self.sessions.slice_indexer(start, end)]

    def asset_start(self, asset_id):
        return asset_start(EQUITY_INFO, asset_id)

    def asset_end(self, asset_id):
        return asset_end(EQUITY_INFO, asset_id)

    def dates_for_asset(self, asset_id):
        start, end = self.asset_start(asset_id), self.asset_end(asset_id)
        return self.trading_days_between(start, end)

    def test_data_frequency(self):
        # DataPortal only reindexes session readers onto its calendar when
        # they report "session".
        assert_equal(self.daily_bar_reader.data_frequency, "session")

    def test_read_first_trading_day(self):
        assert self.daily_bar_reader.first_trading_day == self.sessions[0]

    def test_sessions(self):
        assert_equal(self.daily_bar_reader.sessions, self.sessions)

    def _check_read_results(self, columns, assets, start_date, end_date):
        results = self.daily_bar_reader.load_raw_arrays(
            columns,
            start_date,
            end_date,
            assets,
        )
        dates = self.trading_days_between(start_date, end_date)
        for column, result in zip(columns, results):
            assert_equal(
                result,
                expected_bar_values_2d(
                    dates,
                    assets,
                    EQUITY_INFO.loc[self.assets],
                    column,
                    holes=self.holes,
                ),
            )

    @pytest.mark.parametrize(
        "columns",
        [
            ["open"],
            ["close", "volume"],
            ["volume", "high", "low"],
            ["open", "high", "low", "close", "volume"],
        ],
    )
    def test_read(self, columns):
        self._check_read_results(
            columns,
            self.assets,
            TEST_QUERY_START,
            TEST_QUERY_STOP,
        )

        assets_array = np.array(self.assets)
        for _ in range(5):
            assets = assets_array.copy()
            self.rand.shuffle(assets)
            assets = assets[: np.random.randint(1, len(assets))]
            self._check_read_results(
                columns,
                assets,
                TEST_QUERY_START,
                TEST_QUERY_STOP,
            )

    def test_start_on_asset_start(self):
        """
        Test loading with queries that starts on the first day of each asset's
        lifetime.
        """
        columns = ["high", "volume"]
        for asset in self.assets:
            self._check_read_results(
                columns,
                self.assets,
                start_date=self.asset_start(asset),
                end_date=self.sessions[-1],
            )

    def test_start_on_asset_end(self):
        """
        Test loading with queries that start on the last day of each asset's
        lifetime.
        """
        columns = ["close", "volume"]
        for asset in self.assets:
            self._check_read_results(
                columns,
                self.assets,
                start_date=self.asset_end(asset),
                end_date=self.sessions[-1],
            )

    def test_end_on_asset_start(self):
        """
        Test loading with queries that end on the first day of each asset's
        lifetime.
        """
        columns = ["close", "volume"]
        for asset in self.assets:
            self._check_read_results(
                columns,
                self.assets,
                start_date=self.sessions[0],
                end_date=self.asset_start(asset),
            )

    def test_end_on_asset_end(self):
        """
        Test loading with queries that end on the last day of each asset's
        lifetime.
        """
        columns = [CLOSE, VOLUME]
        for asset in self.assets:
            self._check_read_results(
                columns,
                self.assets,
                start_date=self.sessions[0],
                end_date=self.asset_end(asset),
            )

    def test_read_known_and_unknown_sids(self):
        """
        Test a query with some known sids mixed in with unknown sids.
        """

        # Construct a list of alternating valid and invalid query sids,
        # bookended by invalid sids.
        #
        # E.g.
        #   INVALID VALID INVALID VALID ... VALID INVALID
        query_assets = (
            [self.assets[-1] + 1]
            + list(range(self.assets[0], self.assets[-1] + 1))
            + [self.assets[-1] + 3]
        )

        columns = [CLOSE, VOLUME]
        self._check_read_results(
            columns,
            query_assets,
            start_date=TEST_QUERY_START,
            end_date=TEST_QUERY_STOP,
        )

    @pytest.mark.parametrize(
        "query_assets",
        [
            # Query for only even sids, only odd ids are valid.
            [],
            [2],
            [2, 4, 800],
        ],
    )
    def test_read_only_unknown_sids(self, query_assets):
        columns = [CLOSE, VOLUME]
        with pytest.raises(ValueError):
            self.daily_bar_reader.load_raw_arrays(
                columns,
                TEST_QUERY_START,
                TEST_QUERY_STOP,
                query_assets,
            )

    def test_unadjusted_get_value(self):
        """Test get_value() on both a price field (CLOSE) and VOLUME."""
        reader = self.daily_bar_reader

        def make_failure_msg(asset, date, field):
            return (
                f"Unexpected value for sid={asset}; date={date.date()}; field={field}."
            )

        for asset in self.assets:
            # Dates to check.
            asset_start = self.asset_start(asset)

            asset_dates = self.dates_for_asset(asset)
            asset_middle = asset_dates[len(asset_dates) // 2]

            asset_end = self.asset_end(asset)

            # At beginning
            assert_equal(
                reader.get_value(asset, asset_start, CLOSE),
                expected_bar_value_with_holes(
                    asset_id=asset,
                    date=asset_start,
                    colname=CLOSE,
                    holes=self.holes,
                    missing_value=nan,
                ),
                msg=make_failure_msg(asset, asset_start, CLOSE),
            )

            # Middle
            assert_equal(
                reader.get_value(asset, asset_middle, CLOSE),
                expected_bar_value_with_holes(
                    asset_id=asset,
                    date=asset_middle,
                    colname=CLOSE,
                    holes=self.holes,
                    missing_value=nan,
                ),
                msg=make_failure_msg(asset, asset_middle, CLOSE),
            )

            # End
            assert_equal(
                reader.get_value(asset, asset_end, CLOSE),
                expected_bar_value_with_holes(
                    asset_id=asset,
                    date=asset_end,
                    colname=CLOSE,
                    holes=self.holes,
                    missing_value=nan,
                ),
                msg=make_failure_msg(asset, asset_end, CLOSE),
            )

            # Ensure that volume does not have float adjustment applied.
            assert_equal(
                reader.get_value(asset, asset_start, VOLUME),
                expected_bar_value_with_holes(
                    asset_id=asset,
                    date=asset_start,
                    colname=VOLUME,
                    holes=self.holes,
                    missing_value=0,
                ),
                msg=make_failure_msg(asset, asset_start, VOLUME),
            )

    def test_unadjusted_get_value_no_data(self):
        """Test behavior of get_value() around missing data."""
        reader = self.daily_bar_reader

        for asset in self.assets:
            before_start = self.trading_calendar.previous_session(
                self.asset_start(asset)
            )
            after_end = self.trading_calendar.next_session(self.asset_end(asset))

            # Attempting to get data for an asset before its start date
            # should raise NoDataBeforeDate.
            if TEST_CALENDAR_START <= before_start <= TEST_CALENDAR_STOP:
                with pytest.raises(NoDataBeforeDate):
                    reader.get_value(asset, before_start, CLOSE)

            # Attempting to get data for an asset after its end date
            # should raise NoDataAfterDate.
            if TEST_CALENDAR_START <= after_end <= TEST_CALENDAR_STOP:
                with pytest.raises(NoDataAfterDate):
                    reader.get_value(asset, after_end, CLOSE)

        # Retrieving data for "holes" (dates with no data, but within
        # an  asset's lifetime) should not raise an exception. nan is
        # returned for OHLC fields, and 0 is returned for volume.
        for asset, dates in self.holes.items():
            for date in dates:
                assert_equal(
                    reader.get_value(asset, date, CLOSE),
                    nan,
                    msg=(
                        f"Expected a hole for sid={asset}; date={date.date()}, "
                        "but got a non-nan value for close."
                    ),
                )
                assert_equal(
                    reader.get_value(asset, date, VOLUME),
                    0.0,
                    msg=(
                        f"Expected a hole for sid={asset}; date={date.date()}, "
                        "but got a non-zero value for volume."
                    ),
                )

    def test_get_last_traded_dt(self):
        for sid in self.assets:
            assert_equal(
                self.daily_bar_reader.get_last_traded_dt(
                    self.asset_finder.retrieve_asset(sid),
                    self.EQUITY_DAILY_BAR_END_DATE,
                ),
                self.asset_end(sid),
            )

            # If an asset is alive by ``mid_date``, its "last trade" dt
            # is either the end date for the asset, or ``mid_date`` if
            # the asset is *still* alive at that point. Otherwise, it
            # is pd.NaT.
            mid_date = Timestamp("2015-06-15")
            if self.asset_start(sid) <= mid_date:
                expected = min(self.asset_end(sid), mid_date)
            else:
                expected = NaT

            assert_equal(
                self.daily_bar_reader.get_last_traded_dt(
                    self.asset_finder.retrieve_asset(sid),
                    mid_date,
                ),
                expected,
            )

            # If the dt passed comes before any of the assets
            # start trading, the "last traded" dt for each is pd.NaT.
            assert_equal(
                self.daily_bar_reader.get_last_traded_dt(
                    self.asset_finder.retrieve_asset(sid),
                    Timestamp(0, tz="UTC"),
                ),
                NaT,
            )

    def test_listing_currency(self):
        # Test loading on all assets.
        all_assets = np.array(list(self.assets))
        all_results = self.daily_bar_reader.currency_codes(all_assets)
        all_expected = self.make_equity_daily_bar_currency_codes(
            self.DAILY_BARS_TEST_QUERY_COUNTRY_CODE,
            all_assets,
        ).to_numpy(dtype=object)
        assert_equal(all_results, all_expected)

        assert all_results.dtype == np.dtype(object)
        for code in all_results:
            assert isinstance(code, str)

        # Check all possible subsets of assets.
        for indices in map(list, powerset(range(len(all_assets)))):
            # Empty queries aren't currently supported.
            if not indices:
                continue
            assets = all_assets[indices]
            results = self.daily_bar_reader.currency_codes(assets)
            expected = all_expected[indices]

            assert_equal(results, expected)

    def test_listing_currency_for_nonexistent_asset(self):
        reader = self.daily_bar_reader

        valid_sid = max(self.assets)
        valid_currency = reader.currency_codes(np.array([valid_sid]))[0]
        invalid_sids = [-1, -2]

        # XXX: We currently require at least one valid sid here, because the
        # MultiCountryDailyBarReader needs one valid sid to be able to dispatch
        # to a child reader. We could probably make that work, but there are no
        # real-world cases where we expect to get all-invalid currency queries,
        # so it's unclear whether we should do work to explicitly support such
        # queries.
        mixed = np.array(invalid_sids + [valid_sid])
        result = self.daily_bar_reader.currency_codes(mixed)
        expected = np.array([None] * 2 + [valid_currency])
        assert_equal(result, expected)


class BcolzDailyBarTestCase(WithBcolzEquityDailyBarReader, _DailyBarsTestCase):
    EQUITY_DAILY_BAR_COUNTRY_CODES = ["US"]

    @classmethod
    def init_class_fixtures(cls):
        super().init_class_fixtures()

        cls.daily_bar_reader = cls.bcolz_equity_daily_bar_reader

    def test_write_ohlcv_content(self):
        result = self.bcolz_daily_bar_ctable
        for column in OHLCV:
            idx = 0
            data = result[column][:]
            multiplier = 1 if column == "volume" else 1000
            for asset_id in self.assets:
                for date in self.dates_for_asset(asset_id):
                    assert (
                        data[idx]
                        == expected_bar_value_with_holes(
                            asset_id=asset_id,
                            date=date,
                            colname=column,
                            holes=self.holes,
                            missing_value=0,
                        )
                        * multiplier
                    )
                    idx += 1
            assert idx == len(data)

    def test_write_day_and_id(self):
        result = self.bcolz_daily_bar_ctable
        idx = 0
        ids = result["id"]
        days = result["day"]
        for asset_id in self.assets:
            for date in self.dates_for_asset(asset_id):
                assert ids[idx] == asset_id
                assert date == Timestamp(days[idx], unit="s")
                idx += 1

    def test_write_attrs(self):
        result = self.bcolz_daily_bar_ctable
        expected_first_row = {
            "1": 0,
            "3": 5,  # Asset 1 has 5 trading days.
            "5": 12,  # Asset 3 has 7 trading days.
            "7": 33,  # Asset 5 has 21 trading days.
            "9": 44,  # Asset 7 has 11 trading days.
            "11": 49,  # Asset 9 has 5 trading days.
        }
        expected_last_row = {
            "1": 4,
            "3": 11,
            "5": 32,
            "7": 43,
            "9": 48,
            "11": 57,  # Asset 11 has 9 trading days.
        }
        expected_calendar_offset = {
            "1": 0,  # Starts on 6-01, 1st trading day of month.
            "3": 15,  # Starts on 6-22, 16th trading day of month.
            "5": 1,  # Starts on 6-02, 2nd trading day of month.
            "7": 0,  # Starts on 6-01, 1st trading day of month.
            "9": 9,  # Starts on 6-12, 10th trading day of month.
            "11": 10,  # Starts on 6-15, 11th trading day of month.
        }
        assert result.attrs["first_row"] == expected_first_row
        assert result.attrs["last_row"] == expected_last_row
        assert result.attrs["calendar_offset"] == expected_calendar_offset
        cal = get_calendar(result.attrs["calendar_name"])
        first_session = Timestamp(result.attrs["start_session_ns"])
        end_session = Timestamp(result.attrs["end_session_ns"])
        sessions = cal.sessions_in_range(first_session, end_session)

        assert_equal(self.sessions, sessions)


class BcolzDailyBarAlwaysReadAllTestCase(BcolzDailyBarTestCase):
    """
    Force tests defined in BcolzDailyBarTestCase to always read the entire
    column into memory before selecting desired asset data, when invoking
    `load_raw_array`.
    """

    BCOLZ_DAILY_BAR_READ_ALL_THRESHOLD = 0


class BcolzDailyBarNeverReadAllTestCase(BcolzDailyBarTestCase):
    """
    Force tests defined in BcolzDailyBarTestCase to never read the entire
    column into memory before selecting desired asset data, when invoking
    `load_raw_array`.
    """

    BCOLZ_DAILY_BAR_READ_ALL_THRESHOLD = maxsize


class BcolzDailyBarWriterMissingDataTestCase(
    WithAssetFinder, WithTmpDir, WithTradingCalendars, ZiplineTestCase
):
    # Sid 5 is active from 2015-06-02 to 2015-06-30.
    MISSING_DATA_SID = 5
    # Leave out data for a day in the middle of the query range.
    MISSING_DATA_DAY = Timestamp("2015-06-15")

    @classmethod
    def make_equity_info(cls):
        return EQUITY_INFO.loc[EQUITY_INFO.index == cls.MISSING_DATA_SID].copy()

    def test_missing_values_assertion(self):
        sessions = self.trading_calendar.sessions_in_range(
            TEST_CALENDAR_START,
            TEST_CALENDAR_STOP,
        )

        sessions_with_gap = sessions[sessions != self.MISSING_DATA_DAY]
        bar_data = make_bar_data(self.make_equity_info(), sessions_with_gap)

        writer = BcolzDailyBarWriter(
            self.tmpdir.path,
            self.trading_calendar,
            sessions[0],
            sessions[-1],
        )

        # There are 21 sessions between the start and end date for this
        # asset, and we excluded one.
        expected_msg = re.escape(
            "Got 20 rows for daily bars table with first day=2015-06-02, last "
            "day=2015-06-30, expected 21 rows.\n"
            "Missing sessions: "
            "[Timestamp('2015-06-15 00:00:00')]\n"
            "Extra sessions: []"
        )
        with pytest.raises(AssertionError, match=expected_msg):
            writer.write(bar_data)


class ParquetDailyBarTestCase(WithParquetEquityDailyBarReader, _DailyBarsTestCase):
    EQUITY_DAILY_BAR_COUNTRY_CODES = ["US"]

    @classmethod
    def init_class_fixtures(cls):
        super().init_class_fixtures()
        cls.daily_bar_reader = cls.parquet_equity_daily_bar_reader

    def test_accepts_assets(self):
        # The DataPortal passes Asset objects rather than integer sids.
        assets = self.asset_finder.retrieve_all(self.assets)
        sessions = self.trading_days_between(TEST_QUERY_START, TEST_QUERY_STOP)
        by_asset = self.daily_bar_reader.load_raw_arrays(
            ["close"], sessions[0], sessions[-1], assets
        )
        by_sid = self.daily_bar_reader.load_raw_arrays(
            ["close"], sessions[0], sessions[-1], self.assets
        )
        assert_equal(by_asset, by_sid)
        asset, sid = assets[0], self.assets[0]
        day = self.dates_for_asset(sid)[0]
        assert_equal(
            self.daily_bar_reader.get_value(asset, day, "close"),
            self.daily_bar_reader.get_value(sid, day, "close"),
        )
        assert_equal(
            self.daily_bar_reader.currency_codes(assets),
            self.daily_bar_reader.currency_codes(self.assets),
        )

    def test_small_block_cache(self):
        # Reads must not depend on which blocks happen to be cached.
        reader = ParquetDailyBarReader(self.parquet_daily_bar_path, block_cache_size=1)
        expected = self.daily_bar_reader.load_raw_arrays(
            list(OHLCV), TEST_QUERY_START, TEST_QUERY_STOP, self.assets
        )
        for _ in range(2):
            actual = reader.load_raw_arrays(
                list(OHLCV), TEST_QUERY_START, TEST_QUERY_STOP, self.assets
            )
            for a, e in zip(actual, expected):
                assert_equal(a, e)


class ParquetDailyBarWriterTestCase(WithTmpDir, WithTradingCalendars, ZiplineTestCase):
    START = Timestamp("2015-12-28")
    END = Timestamp("2016-01-08")

    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.sessions = self.trading_calendar.sessions_in_range(self.START, self.END)
        self.path = self.tmpdir.getpath(f"parquet-{id(self)}")

    def frame(self, sessions, close=10.0, volume=100):
        return DataFrame(
            {
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": volume,
            },
            index=sessions,
        )

    def write(self, data, **kwargs):
        ParquetDailyBarWriter(
            self.path, self.trading_calendar, self.START, self.END
        ).write(data, **kwargs)
        return ParquetDailyBarReader(self.path)

    def test_gaps_are_missing_data(self):
        # Sessions between an asset's first and last bar that have no data are
        # stored as missing, across a year boundary.
        with_gap = self.frame(self.sessions.delete([2, 4]))
        reader = self.write([(1, with_gap)])
        close, volume = reader.load_raw_arrays(
            ["close", "volume"], self.sessions[0], self.sessions[-1], [1]
        )
        expected_close = np.full(len(self.sessions), 10.0)
        expected_close[[2, 4]] = np.nan
        expected_volume = np.full(len(self.sessions), 100.0)
        expected_volume[[2, 4]] = 0
        assert_equal(close[:, 0], expected_close)
        assert_equal(volume[:, 0], expected_volume)
        assert_equal(reader.get_value(1, self.sessions[2], "close"), np.nan)
        assert_equal(reader.get_last_traded_dt(1, self.sessions[4]), self.sessions[3])

    def test_zero_price_is_missing(self):
        frame = self.frame(self.sessions)
        frame.iloc[1, frame.columns.get_loc("close")] = 0.0
        reader = self.write([(1, frame)])
        assert_equal(reader.get_value(1, self.sessions[1], "close"), np.nan)

    def test_volume_beyond_uint32(self):
        # bcolz stored volume as uint32; the Parquet format doesn't overflow.
        big = 10_000_000_000
        reader = self.write([(1, self.frame(self.sessions, volume=big))])
        assert_equal(reader.get_value(1, self.sessions[0], "volume"), float(big))

    def test_invalid_data(self):
        frame = self.frame(self.sessions)
        frame.iloc[0, frame.columns.get_loc("open")] = -1.0
        with pytest.raises(ValueError):
            self.write([(1, frame)], invalid_data_behavior="raise")

    def test_rejects_non_sessions(self):
        frame = self.frame(DatetimeIndex([Timestamp("2016-01-02")]))  # Saturday
        with pytest.raises(ValueError, match="not sessions"):
            self.write([(1, frame)])

    def test_rejects_unknown_asset(self):
        with pytest.raises(ValueError, match="unknown asset id 2"):
            self.write([(2, self.frame(self.sessions))], assets={1})

    def test_refuses_to_overwrite(self):
        self.write([(1, self.frame(self.sessions))])
        with pytest.raises(ValueError, match="is not empty"):
            self.write([(1, self.frame(self.sessions))])

    def test_newer_format_version(self):
        self.write([(1, self.frame(self.sessions))])
        metadata_path = f"{self.path}/_metadata.json"
        with open(metadata_path) as f:
            metadata = json.load(f)
        metadata["version"] = FORMAT_VERSION + 1
        with open(metadata_path, "w") as f:
            json.dump(metadata, f)
        reader = ParquetDailyBarReader(self.path)
        with pytest.raises(ValueError, match="format version"):
            reader.get_value(1, self.sessions[0], "close")

    def test_readable_as_dataset(self):
        self.write([(1, self.frame(self.sessions)), (2, self.frame(self.sessions))])
        frame = read_parquet(self.path, columns=["sid", "day", "close"])
        assert_equal(len(frame), 2 * len(self.sessions))
        assert_equal(sorted(frame["sid"].unique()), [1, 2])

    def test_opens_lazily(self):
        reader = ParquetDailyBarReader(self.path)
        self.write([(1, self.frame(self.sessions))])
        assert_equal(reader.sessions, self.sessions)


class _MultiCountryDailyBarTestCase(WithTmpDir, _DailyBarsTestCase):
    """The daily bar tests on a MultiCountryDailyBarReader over one Parquet
    dataset per country.
    """

    @classmethod
    def init_class_fixtures(cls):
        super().init_class_fixtures()
        readers = {}
        for country_code in cls.EQUITY_DAILY_BAR_COUNTRY_CODES:
            sids = cls.asset_finder.equities_sids_for_country_code(country_code)
            path = cls.tmpdir.getpath(f"daily_bars_{country_code}.parquet")
            days = cls.equity_daily_bar_days
            ParquetDailyBarWriter(path, cls.trading_calendar, days[0], days[-1]).write(
                cls.make_equity_daily_bar_data(country_code=country_code, sids=sids),
                currency_codes=cls.make_equity_daily_bar_currency_codes(
                    country_code, sids
                ),
            )
            readers[country_code] = ParquetDailyBarReader(path)
        cls.daily_bar_reader = MultiCountryDailyBarReader(readers)

    def test_invalid_date(self):
        INVALID_DATES = (
            # Before the start of the daily bars.
            self.trading_calendar.previous_session(TEST_CALENDAR_START),
            # A Sunday.
            Timestamp("2015-06-07"),
            # After the end of the daily bars.
            self.trading_calendar.next_session(TEST_CALENDAR_STOP),
        )

        for invalid_date in INVALID_DATES:
            with pytest.raises(NoDataOnDate):
                self.daily_bar_reader.load_raw_arrays(
                    OHLCV,
                    invalid_date,
                    TEST_QUERY_STOP,
                    self.assets,
                )

            with pytest.raises(NoDataOnDate):
                self.daily_bar_reader.get_value(
                    self.assets[0],
                    invalid_date,
                    "close",
                )

    def test_countries(self):
        assert_equal(set(self.daily_bar_reader.countries), {"US", "CA"})

    def test_multi_country_reads_unsupported(self):
        us, ca = (
            self.asset_finder.equities_sids_for_country_code(country)[0]
            for country in ("US", "CA")
        )
        with pytest.raises(NotImplementedError, match="multiple countries"):
            self.daily_bar_reader.load_raw_arrays(
                OHLCV, TEST_QUERY_START, TEST_QUERY_STOP, [us, ca]
            )


class MultiCountryDailyBarUSTestCase(_MultiCountryDailyBarTestCase):
    DAILY_BARS_TEST_QUERY_COUNTRY_CODE = "US"


class MultiCountryDailyBarCanadaTestCase(_MultiCountryDailyBarTestCase):
    TRADING_CALENDAR_PRIMARY_CAL = "TSX"
    DAILY_BARS_TEST_QUERY_COUNTRY_CODE = "CA"
