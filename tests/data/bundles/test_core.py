import os
import shutil
import sys
from unittest import mock

import pandas as pd
import sqlalchemy as sa
import toolz.curried.operator as op
from parameterized import parameterized
from toolz import valmap

import zipline.utils.paths as pth
from zipline.assets import ASSET_DB_VERSION
from zipline.assets.asset_writer import check_version_info
from zipline.assets.synthetic import make_simple_equity_info
from zipline.data.bcolz_daily_bars import BcolzDailyBarReader, BcolzDailyBarWriter
from zipline.data.bcolz_minute_bars import (
    US_EQUITIES_MINUTES_PER_DAY,
    BcolzMinuteBarReader,
    BcolzMinuteBarWriter,
)
from zipline.data.bundles import (
    UnknownBundle,
    from_bundle_ingest_dirname,
    ingestions_for_bundle,
)
from zipline.data.bundles.core import (
    BadClean,
    _make_bundle_core,
    asset_db_path,
    bcolz_daily_equity_relative,
    bcolz_minute_equity_relative,
    convert,
    daily_equity_path,
    minute_equity_path,
    to_bundle_ingest_dirname,
)
from zipline.data.parquet_daily_bars import ParquetDailyBarReader
from zipline.data.parquet_minute_bars import ParquetMinuteBarReader
from zipline.lib.adjustment import Float64Multiply
from zipline.pipeline.loaders.synthetic import (
    expected_bar_values_2d,
    make_bar_data,
)
from zipline.testing import (
    str_to_seconds,
    subtest,
)
from zipline.testing.fixtures import (
    WithDefaultDateBounds,
    WithInstanceTmpDir,
    ZiplineTestCase,
)
from zipline.testing.predicates import (
    assert_equal,
    assert_false,
    assert_in,
    assert_is,
    assert_is_instance,
    assert_is_none,
    assert_raises,
    assert_true,
)
from zipline.utils.cache import dataframe_cache
from zipline.utils.calendar_utils import ExchangeCalendar, get_calendar
from zipline.utils.functional import apply

_1_ns = pd.Timedelta(1, unit="ns")


class BundleCoreTestCase(WithInstanceTmpDir, WithDefaultDateBounds, ZiplineTestCase):
    START_DATE = pd.Timestamp("2014-01-06")
    END_DATE = pd.Timestamp("2014-01-10")

    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        # Output dirs written by the bundle that ``_empty_ingest`` registers.
        self._empty_ingest_wrote_to = []
        (
            self.bundles,
            self.register,
            self.unregister,
            self.ingest,
            self.load,
            self.clean,
        ) = _make_bundle_core()
        self.environ = {"ZIPLINE_ROOT": self.instance_tmpdir.path}

    def test_register_decorator(self):
        @apply
        @subtest(((c,) for c in "abcde"), "name")
        def _(name):
            @self.register(name)
            def ingest(*args):
                pass

            assert_in(name, self.bundles)
            assert_is(self.bundles[name].ingest, ingest)

        self._check_bundles(set("abcde"))

    def test_register_minutes_per_day_is_deprecated(self):
        def ingest(*args):
            pass

        with self.assertWarnsRegex(DeprecationWarning, "minutes_per_day"):
            self.register("bundle", ingest, minutes_per_day=390)

    def test_register_call(self):
        def ingest(*args):
            pass

        @apply
        @subtest(((c,) for c in "abcde"), "name")
        def _(name):
            self.register(name, ingest)
            assert_in(name, self.bundles)
            assert_is(self.bundles[name].ingest, ingest)

        assert_equal(
            valmap(op.attrgetter("ingest"), self.bundles),
            {k: ingest for k in "abcde"},
        )
        self._check_bundles(set("abcde"))

    def _check_bundles(self, names):
        assert_equal(set(self.bundles.keys()), names)

        for name in names:
            self.unregister(name)

        assert_false(self.bundles)

    def test_register_no_create(self):
        called = [False]

        @self.register("bundle", create_writers=False)
        def bundle_ingest(
            environ,
            asset_db_writer,
            minute_bar_writer,
            daily_bar_writer,
            adjustment_writer,
            calendar,
            start_session,
            end_session,
            cache,
            show_progress,
            output_dir,
        ):
            assert_is_none(asset_db_writer)
            assert_is_none(minute_bar_writer)
            assert_is_none(daily_bar_writer)
            assert_is_none(adjustment_writer)
            called[0] = True

        self.ingest("bundle", self.environ)
        assert_true(called[0])

    def test_ingest(self):
        calendar = get_calendar("XNYS")
        sessions = calendar.sessions_in_range(self.START_DATE, self.END_DATE)
        minutes = calendar.sessions_minutes(
            self.START_DATE,
            self.END_DATE,
        )

        sids = tuple(range(3))
        equities = make_simple_equity_info(
            sids,
            self.START_DATE,
            self.END_DATE,
        )

        daily_bar_data = make_bar_data(equities, sessions)
        minute_bar_data = make_bar_data(equities, minutes)
        first_split_ratio = 0.5
        second_split_ratio = 0.1
        splits = pd.DataFrame.from_records(
            [
                {
                    "effective_date": str_to_seconds("2014-01-08"),
                    "ratio": first_split_ratio,
                    "sid": 0,
                },
                {
                    "effective_date": str_to_seconds("2014-01-09"),
                    "ratio": second_split_ratio,
                    "sid": 1,
                },
            ]
        )

        @self.register(
            "bundle",
            calendar_name="NYSE",
            start_session=self.START_DATE,
            end_session=self.END_DATE,
        )
        def bundle_ingest(
            environ,
            asset_db_writer,
            minute_bar_writer,
            daily_bar_writer,
            adjustment_writer,
            calendar,
            start_session,
            end_session,
            cache,
            show_progress,
            output_dir,
        ):
            assert_is(environ, self.environ)

            asset_db_writer.write(equities=equities)
            minute_bar_writer.write(minute_bar_data)
            daily_bar_writer.write(daily_bar_data)
            adjustment_writer.write(splits=splits)

            assert_is_instance(calendar, ExchangeCalendar)
            assert_is_instance(cache, dataframe_cache)
            assert_is_instance(show_progress, bool)

        self.ingest("bundle", environ=self.environ)
        bundle = self.load("bundle", environ=self.environ)
        self.add_instance_callback(bundle.close)

        assert_equal(set(bundle.asset_finder.sids), set(sids))
        assert_is_instance(bundle.equity_daily_bar_reader, ParquetDailyBarReader)
        assert_is_instance(bundle.equity_minute_bar_reader, ParquetMinuteBarReader)

        columns = "open", "high", "low", "close", "volume"

        actual = bundle.equity_minute_bar_reader.load_raw_arrays(
            columns,
            minutes[0],
            minutes[-1],
            sids,
        )

        for actual_column, colname in zip(actual, columns):
            assert_equal(
                actual_column,
                expected_bar_values_2d(minutes, sids, equities, colname),
                msg=colname,
            )

        actual = bundle.equity_daily_bar_reader.load_raw_arrays(
            columns,
            self.START_DATE,
            self.END_DATE,
            sids,
        )
        for actual_column, colname in zip(actual, columns):
            assert_equal(
                actual_column,
                expected_bar_values_2d(sessions, sids, equities, colname),
                msg=colname,
            )
        adjs_for_cols = bundle.adjustment_reader.load_pricing_adjustments(
            columns,
            sessions,
            pd.Index(sids),
        )
        for column, adjustments in zip(columns, adjs_for_cols[:-1]):
            # iterate over all the adjustments but `volume`
            assert_equal(
                adjustments,
                {
                    2: [
                        Float64Multiply(
                            first_row=0,
                            last_row=2,
                            first_col=0,
                            last_col=0,
                            value=first_split_ratio,
                        )
                    ],
                    3: [
                        Float64Multiply(
                            first_row=0,
                            last_row=3,
                            first_col=1,
                            last_col=1,
                            value=second_split_ratio,
                        )
                    ],
                },
                msg=column,
            )

        # check the volume, the value should be 1/ratio
        assert_equal(
            adjs_for_cols[-1],
            {
                2: [
                    Float64Multiply(
                        first_row=0,
                        last_row=2,
                        first_col=0,
                        last_col=0,
                        value=1 / first_split_ratio,
                    )
                ],
                3: [
                    Float64Multiply(
                        first_row=0,
                        last_row=3,
                        first_col=1,
                        last_col=1,
                        value=1 / second_split_ratio,
                    )
                ],
            },
            msg="volume",
        )

    def _ingest_daily(self, write_daily=True, dividends=None):
        """Ingest a bundle of three equities and load it.

        Returns the bundle, the equities' sids and the daily bars' sessions.
        """
        calendar = get_calendar("XNYS")
        sessions = calendar.sessions_in_range(self.START_DATE, self.END_DATE)
        sids = tuple(range(3))
        equities = make_simple_equity_info(sids, self.START_DATE, self.END_DATE)

        @self.register(
            "bundle",
            calendar_name="NYSE",
            start_session=self.START_DATE,
            end_session=self.END_DATE,
        )
        def bundle_ingest(
            environ,
            asset_db_writer,
            minute_bar_writer,
            daily_bar_writer,
            adjustment_writer,
            *args,
        ):
            asset_db_writer.write(equities=equities)
            if write_daily:
                daily_bar_writer.write(make_bar_data(equities, sessions))
            adjustment_writer.write(dividends=dividends)

        self.ingest("bundle", environ=self.environ)
        bundle = self.load("bundle", environ=self.environ)
        self.add_instance_callback(bundle.close)
        return bundle, sids, sessions

    def test_ingest_dividend_ratios_from_daily_bars(self):
        ex_date = pd.Timestamp("2014-01-08")
        dividends = pd.DataFrame(
            {
                "sid": [0],
                "amount": [0.5],
                "ex_date": [ex_date],
                "record_date": [ex_date],
                "declared_date": [ex_date],
                "pay_date": [ex_date],
            }
        )
        bundle, _, _ = self._ingest_daily(dividends=dividends)

        previous_close = bundle.equity_daily_bar_reader.get_value(
            0, pd.Timestamp("2014-01-07"), "close"
        )
        assert_equal(
            bundle.adjustment_reader.get_adjustments_for_sid("dividends", 0),
            [[ex_date, 1.0 - 0.5 / previous_close]],
        )

    def test_ingest_without_daily_bars(self):
        bundle, sids, sessions = self._ingest_daily(write_daily=False)

        reader = bundle.equity_daily_bar_reader
        assert_equal(reader.sessions, sessions)
        assert_equal(reader.currency_codes(sids).tolist(), [None] * len(sids))

    def test_ingest_without_minute_bars(self):
        _, _, sessions = self._ingest_daily()
        bundle = self.load("bundle", environ=self.environ)
        self.add_instance_callback(bundle.close)

        reader = bundle.equity_minute_bar_reader
        assert_is_instance(reader, ParquetMinuteBarReader)
        calendar = get_calendar("XNYS")
        assert_equal(
            reader.last_available_dt, calendar.session_last_minute(sessions[-1])
        )

    def _make_bcolz_ingestion(self):
        """Ingest a bundle, then store its bars with bcolz, as zipline did
        before 2.0.

        Returns the sids, sessions, minutes and equities of the bundle.
        """
        _, sids, sessions = self._ingest_daily()
        (timestr,) = ingestions_for_bundle("bundle", environ=self.environ)
        timestr = to_bundle_ingest_dirname(timestr)
        calendar = get_calendar("XNYS")
        minutes = calendar.sessions_minutes(sessions[0], sessions[-1])
        equities = make_simple_equity_info(sids, self.START_DATE, self.END_DATE)

        shutil.rmtree(daily_equity_path("bundle", timestr, environ=self.environ))
        BcolzDailyBarWriter(
            pth.data_path(
                bcolz_daily_equity_relative("bundle", timestr), environ=self.environ
            ),
            calendar,
            sessions[0],
            sessions[-1],
        ).write(make_bar_data(equities, sessions))
        shutil.rmtree(minute_equity_path("bundle", timestr, environ=self.environ))
        bcolz_minute_path = pth.data_path(
            bcolz_minute_equity_relative("bundle", timestr), environ=self.environ
        )
        os.makedirs(bcolz_minute_path)
        BcolzMinuteBarWriter(
            bcolz_minute_path,
            calendar,
            sessions[0],
            sessions[-1],
            US_EQUITIES_MINUTES_PER_DAY,
        ).write(make_bar_data(equities, minutes))
        return sids, sessions, minutes, equities

    def _check_bars(self, bundle, sids, sessions, minutes, equities):
        (close,) = bundle.equity_daily_bar_reader.load_raw_arrays(
            ["close"], sessions[0], sessions[-1], sids
        )
        assert_equal(close, expected_bar_values_2d(sessions, sids, equities, "close"))
        (close,) = bundle.equity_minute_bar_reader.load_raw_arrays(
            ["close"], minutes[0], minutes[-1], sids
        )
        assert_equal(close, expected_bar_values_2d(minutes, sids, equities, "close"))

    def test_load_bcolz_ingestion(self):
        """Bundles ingested before bars moved to Parquet still load."""
        sids, sessions, minutes, equities = self._make_bcolz_ingestion()

        with self.assertWarnsRegex(FutureWarning, "zipline convert -b bundle"):
            bundle = self.load("bundle", environ=self.environ)
        self.add_instance_callback(bundle.close)
        assert_is_instance(bundle.equity_daily_bar_reader, BcolzDailyBarReader)
        assert_is_instance(bundle.equity_minute_bar_reader, BcolzMinuteBarReader)
        self._check_bars(bundle, sids, sessions, minutes, equities)

    def test_bcolz_ingestion_without_bcolz(self):
        """Without the optional bcolz support, old ingestions explain what to
        install instead of failing on an import.
        """
        self._make_bcolz_ingestion()
        with mock.patch.dict(sys.modules, {"bcolz": None}):
            with self.assertRaisesRegex(ImportError, r"zipline\[bcolz\]"):
                self.load("bundle", environ=self.environ)
            with self.assertRaisesRegex(ImportError, r"zipline\[bcolz\]"):
                convert("bundle", self.environ)

    @parameterized.expand([(False,), (True,)])
    def test_convert(self, delete_bcolz):
        sids, sessions, minutes, equities = self._make_bcolz_ingestion()
        (timestr,) = ingestions_for_bundle("bundle", environ=self.environ)
        timestr = to_bundle_ingest_dirname(timestr)

        converted = convert("bundle", self.environ, delete_bcolz=delete_bcolz)
        assert_equal(
            converted,
            [
                daily_equity_path("bundle", timestr, environ=self.environ),
                minute_equity_path("bundle", timestr, environ=self.environ),
            ],
        )
        for relative in (bcolz_daily_equity_relative, bcolz_minute_equity_relative):
            path = pth.data_path(relative("bundle", timestr), environ=self.environ)
            assert_equal(os.path.exists(path), not delete_bcolz)

        bundle = self.load("bundle", environ=self.environ)
        self.add_instance_callback(bundle.close)
        assert_is_instance(bundle.equity_daily_bar_reader, ParquetDailyBarReader)
        assert_is_instance(bundle.equity_minute_bar_reader, ParquetMinuteBarReader)
        self._check_bars(bundle, sids, sessions, minutes, equities)

        # Converting again finds nothing to do.
        assert_equal(convert("bundle", self.environ), [])

    def test_ingest_assets_versions(self):
        versions = (1, 2)

        called = [False]

        @self.register("bundle", create_writers=False)
        def bundle_ingest_no_create_writers(*args, **kwargs):
            called[0] = True

        now = pd.Timestamp.now("UTC")
        with self.assertRaisesRegex(
            ValueError, "ingest .* creates writers .* downgrade"
        ):
            self.ingest(
                "bundle",
                self.environ,
                assets_versions=versions,
                timestamp=now - pd.Timedelta(seconds=1),
            )
        assert_false(called[0])
        assert_equal(len(ingestions_for_bundle("bundle", self.environ)), 1)

        @self.register("bundle", create_writers=True)
        def bundle_ingest_create_writers(
            environ,
            asset_db_writer,
            minute_bar_writer,
            daily_bar_writer,
            adjustment_writer,
            calendar,
            start_session,
            end_session,
            cache,
            show_progress,
            output_dir,
        ):
            self.assertIsNotNone(asset_db_writer)
            self.assertIsNotNone(minute_bar_writer)
            self.assertIsNotNone(daily_bar_writer)
            self.assertIsNotNone(adjustment_writer)

            equities = make_simple_equity_info(
                tuple(range(3)),
                self.START_DATE,
                self.END_DATE,
            )
            asset_db_writer.write(equities=equities)
            called[0] = True

        # Explicitly use different timestamp; otherwise, test could run so fast
        # that first ingestion is re-used.
        self.ingest("bundle", self.environ, assets_versions=versions, timestamp=now)
        assert_true(called[0])

        ingestions = ingestions_for_bundle("bundle", self.environ)
        assert_equal(len(ingestions), 2)
        for version in sorted(set(versions) | {ASSET_DB_VERSION}):
            eng = sa.create_engine(
                "sqlite:///"
                + asset_db_path(
                    "bundle",
                    to_bundle_ingest_dirname(ingestions[0]),  # most recent
                    self.environ,
                    version,
                )
            )
            metadata = sa.MetaData()
            metadata.reflect(eng)
            version_table = metadata.tables["version_info"]
            check_version_info(eng, version_table, version)
            eng.dispose()

    @parameterized.expand([("clean",), ("load",)])
    def test_bundle_doesnt_exist(self, fnname):
        with assert_raises(UnknownBundle) as e:
            getattr(self, fnname)("ayy", environ=self.environ)

        assert_equal(e.exception.name, "ayy")

    def test_load_no_data(self):
        # register but do not ingest data
        self.register("bundle", lambda *args: None)

        ts = pd.Timestamp("2014")

        with assert_raises(ValueError) as e:
            self.load("bundle", timestamp=ts, environ=self.environ)

        assert_in(
            f"no data for bundle 'bundle' on or before {ts}",
            str(e.exception),
        )

    def _list_bundle(self):
        return {
            os.path.join(pth.data_path(["bundle", d], environ=self.environ))
            for d in os.listdir(
                pth.data_path(["bundle"], environ=self.environ),
            )
        }

    def _empty_ingest(self):
        """Run the nth empty ingest.

        Returns
        -------
        wrote_to : str
            The timestr of the bundle written.
        """
        _wrote_to = self._empty_ingest_wrote_to
        if not self.bundles:

            @self.register(
                "bundle",
                calendar_name="NYSE",
                start_session=pd.Timestamp("2014-01-02"),
                end_session=pd.Timestamp("2014-01-02"),
            )
            def _(
                environ,
                asset_db_writer,
                minute_bar_writer,
                daily_bar_writer,
                adjustment_writer,
                calendar,
                start_session,
                end_session,
                cache,
                show_progress,
                output_dir,
            ):
                _wrote_to.append(output_dir)

        _wrote_to[:] = []
        self.ingest("bundle", environ=self.environ)
        assert_equal(len(_wrote_to), 1, msg="ingest was called more than once")
        ingestions = self._list_bundle()
        assert_in(
            _wrote_to[0],
            ingestions,
            msg="output_dir was not in the bundle directory",
        )
        return _wrote_to[0]

    def test_clean_keep_last(self):
        first = self._empty_ingest()

        assert_equal(
            self.clean("bundle", keep_last=1, environ=self.environ),
            set(),
        )
        assert_equal(
            self._list_bundle(),
            {first},
            msg="directory should not have changed",
        )

        second = self._empty_ingest()
        assert_equal(
            self._list_bundle(),
            {first, second},
            msg="two ingestions are not present",
        )
        assert_equal(
            self.clean("bundle", keep_last=1, environ=self.environ),
            {first},
        )
        assert_equal(
            self._list_bundle(),
            {second},
            msg="first ingestion was not removed with keep_last=2",
        )

        third = self._empty_ingest()
        fourth = self._empty_ingest()
        fifth = self._empty_ingest()

        assert_equal(
            self._list_bundle(),
            {second, third, fourth, fifth},
            msg="larger set of ingestions did not happen correctly",
        )

        assert_equal(
            self.clean("bundle", keep_last=2, environ=self.environ),
            {second, third},
        )

        assert_equal(
            self._list_bundle(),
            {fourth, fifth},
            msg="keep_last=2 did not remove the correct number of ingestions",
        )

        with assert_raises(BadClean):
            self.clean("bundle", keep_last=-1, environ=self.environ)

        assert_equal(
            self._list_bundle(),
            {fourth, fifth},
            msg="keep_last=-1 removed some ingestions",
        )

        assert_equal(
            self.clean("bundle", keep_last=0, environ=self.environ),
            {fourth, fifth},
        )

        assert_equal(
            self._list_bundle(),
            set(),
            msg="keep_last=0 did not remove the correct number of ingestions",
        )

    @staticmethod
    def _ts_of_run(run):
        return from_bundle_ingest_dirname(run.rsplit(os.path.sep, 1)[-1])

    def test_clean_before_after(self):
        first = self._empty_ingest()
        assert_equal(
            self.clean(
                "bundle",
                before=self._ts_of_run(first),
                environ=self.environ,
            ),
            set(),
        )
        assert_equal(
            self._list_bundle(),
            {first},
            msg="directory should not have changed (before)",
        )

        assert_equal(
            self.clean(
                "bundle",
                after=self._ts_of_run(first),
                environ=self.environ,
            ),
            set(),
        )
        assert_equal(
            self._list_bundle(),
            {first},
            msg="directory should not have changed (after)",
        )

        assert_equal(
            self.clean(
                "bundle",
                before=self._ts_of_run(first) + _1_ns,
                environ=self.environ,
            ),
            {first},
        )
        assert_equal(
            self._list_bundle(),
            set(),
            msg="directory now be empty (before)",
        )

        second = self._empty_ingest()
        assert_equal(
            self.clean(
                "bundle",
                after=self._ts_of_run(second) - _1_ns,
                environ=self.environ,
            ),
            {second},
        )
        assert_equal(
            self._list_bundle(),
            set(),
            msg="directory now be empty (after)",
        )

        third = self._empty_ingest()
        fourth = self._empty_ingest()
        fifth = self._empty_ingest()
        sixth = self._empty_ingest()

        assert_equal(
            self._list_bundle(),
            {third, fourth, fifth, sixth},
            msg="larger set of ingestions did no happen correctly",
        )

        assert_equal(
            self.clean(
                "bundle",
                before=self._ts_of_run(fourth),
                after=self._ts_of_run(fifth),
                environ=self.environ,
            ),
            {third, sixth},
        )

        assert_equal(
            self._list_bundle(),
            {fourth, fifth},
            msg="did not strip first and last directories",
        )
