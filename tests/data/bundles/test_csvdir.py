import os
import shutil

import numpy as np
import pandas as pd

from zipline.data.bundles import bundles, ingest, load
from zipline.testing import test_resource_path
from zipline.testing.fixtures import WithInstanceTmpDir, ZiplineTestCase
from zipline.testing.predicates import assert_equal
from zipline.utils.calendar_utils import get_calendar
from zipline.utils.functional import apply


class CSVDIRBundleTestCase(WithInstanceTmpDir, ZiplineTestCase):
    symbols = "AAPL", "IBM", "KO", "MSFT"
    asset_start = pd.Timestamp("2012-01-03")
    asset_end = pd.Timestamp("2014-12-31")
    bundle = bundles["csvdir"]
    calendar = get_calendar(bundle.calendar_name)
    start_date = calendar.first_session
    end_date = calendar.last_session
    api_key = "ayylmao"
    columns = "open", "high", "low", "close", "volume"

    def _expected_data(self, asset_finder):
        sids = {
            symbol: asset_finder.lookup_symbol(
                symbol,
                self.asset_start,
            ).sid
            for symbol in self.symbols
        }

        def per_symbol(symbol):
            df = pd.read_csv(
                test_resource_path(
                    "csvdir_samples", "csvdir", "daily", symbol + ".csv.gz"
                ),
                parse_dates=["date"],
                index_col="date",
                usecols=[
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "date",
                    "dividend",
                    "split",
                ],
                na_values=["NA"],
            )
            df["sid"] = sids[symbol]
            return df

        all_ = (
            pd.concat(map(per_symbol, self.symbols))
            .set_index(
                "sid",
                append=True,
            )
            .unstack()
        )

        # fancy list comprehension with statements
        @list
        @apply
        def pricing():
            for column in self.columns:
                vs = all_[column].values
                if column == "volume":
                    vs = np.nan_to_num(vs)
                yield vs

        adjustments = [
            [
                5572,
                5576,
                5595,
                5634,
                5639,
                5659,
                5698,
                5699,
                5701,
                5702,
                5722,
                5760,
                5764,
                5774,
                5821,
                5822,
                5829,
                5845,
                5884,
                5885,
                5888,
                5908,
                5947,
                5948,
                5951,
                5972,
                6011,
                6020,
                6026,
                6073,
                6080,
                6096,
                6135,
                6136,
                6139,
                6157,
                6160,
                6198,
                6199,
                6207,
                6223,
                6263,
                6271,
                6277,
            ],
            [
                5572,
                5576,
                5595,
                5634,
                5639,
                5659,
                5698,
                5699,
                5701,
                5702,
                5722,
                5760,
                5764,
                5774,
                5821,
                5822,
                5829,
                5845,
                5884,
                5885,
                5888,
                5908,
                5947,
                5948,
                5951,
                5972,
                6011,
                6020,
                6026,
                6073,
                6080,
                6096,
                6135,
                6136,
                6139,
                6157,
                6160,
                6198,
                6199,
                6207,
                6223,
                6263,
                6271,
                6277,
            ],
            [
                5572,
                5576,
                5595,
                5634,
                5639,
                5659,
                5698,
                5699,
                5701,
                5702,
                5722,
                5760,
                5764,
                5774,
                5821,
                5822,
                5829,
                5845,
                5884,
                5885,
                5888,
                5908,
                5947,
                5948,
                5951,
                5972,
                6011,
                6020,
                6026,
                6073,
                6080,
                6096,
                6135,
                6136,
                6139,
                6157,
                6160,
                6198,
                6199,
                6207,
                6223,
                6263,
                6271,
                6277,
            ],
            [
                5572,
                5576,
                5595,
                5634,
                5639,
                5659,
                5698,
                5699,
                5701,
                5702,
                5722,
                5760,
                5764,
                5774,
                5821,
                5822,
                5829,
                5845,
                5884,
                5885,
                5888,
                5908,
                5947,
                5948,
                5951,
                5972,
                6011,
                6020,
                6026,
                6073,
                6080,
                6096,
                6135,
                6136,
                6139,
                6157,
                6160,
                6198,
                6199,
                6207,
                6223,
                6263,
                6271,
                6277,
            ],
            [5701, 6157],
        ]

        return pricing, adjustments

    def environ(self, csvdir):
        return {
            "CSVDIR": csvdir,
            "ZIPLINE_ROOT": self.instance_tmpdir.getpath("zipline_root"),
        }

    def test_bundle(self):
        environ = self.environ(test_resource_path("csvdir_samples", "csvdir"))

        ingest("csvdir", environ=environ)
        bundle = load("csvdir", environ=environ)
        self.add_instance_callback(bundle.close)
        sids = 0, 1, 2, 3
        assert_equal(set(bundle.asset_finder.sids), set(sids))

        for equity in bundle.asset_finder.retrieve_all(sids):
            assert_equal(equity.start_date, self.asset_start, msg=equity)
            assert_equal(equity.end_date, self.asset_end, msg=equity)

        sessions = self.calendar.sessions
        actual = bundle.equity_daily_bar_reader.load_raw_arrays(
            self.columns,
            sessions[sessions.get_indexer([self.asset_start], method="bfill")[0]],
            sessions[sessions.get_indexer([self.asset_end], method="ffill")[0]],
            sids,
        )

        expected_pricing, expected_adjustments = self._expected_data(
            bundle.asset_finder,
        )
        assert_equal(actual, expected_pricing, array_decimal=2)

        adjs_for_cols = bundle.adjustment_reader.load_pricing_adjustments(
            self.columns,
            sessions,
            pd.Index(sids),
        )
        assert_equal(
            [sorted(adj.keys()) for adj in adjs_for_cols], expected_adjustments
        )

    def test_daily_and_minute(self):
        csvdir = self.instance_tmpdir.getpath("csvdir")
        daily = os.path.join(csvdir, "daily")
        minute = os.path.join(csvdir, "minute")
        os.makedirs(daily)
        os.makedirs(minute)
        for symbol in "AAPL", "IBM":
            shutil.copy(
                test_resource_path(
                    "csvdir_samples", "csvdir", "daily", symbol + ".csv.gz"
                ),
                daily,
            )
        # "PL.csv" is a substring of "AAPL.csv.gz".
        with open(os.path.join(daily, "PL.csv"), "w") as f:
            f.write(
                "date,open,high,low,close,volume\n"
                "2013-03-01,10,11,9,10.5,100\n"
                "2013-03-04,11,12,10,11.5,200\n"
            )
        # Minute bars for IBM after its last daily bar. The split in them is
        # ignored, since adjustments come from the daily files.
        with open(os.path.join(minute, "IBM.csv"), "w") as f:
            f.write(
                "date,open,high,low,close,volume,dividend,split\n"
                "2015-01-02 14:31:00,160,161,159,160.5,1000,0.0,1.0\n"
                "2015-01-02 21:00:00,161,162,160,161.5,2000,0.0,2.0\n"
            )
        # A symbol with only minute bars.
        with open(os.path.join(minute, "ZZZ.csv"), "w") as f:
            f.write(
                "date,open,high,low,close,volume\n"
                "2014-06-02 13:31:00,20,21,19,20.5,300\n"
                "2014-06-03 20:00:00,21,22,20,21.5,400\n"
            )

        environ = self.environ(csvdir)
        ingest("csvdir", environ=environ)
        bundle = load("csvdir", environ=environ)
        self.add_instance_callback(bundle.close)

        finder = bundle.asset_finder
        aapl, ibm, pl, zzz = finder.retrieve_all([0, 1, 2, 3])
        assert_equal(
            [aapl.symbol, ibm.symbol, pl.symbol, zzz.symbol],
            ["AAPL", "IBM", "PL", "ZZZ"],
        )
        # Lifetimes span both time frames, in sessions.
        assert_equal(ibm.start_date, self.asset_start)
        assert_equal(ibm.end_date, pd.Timestamp("2015-01-02"))
        assert_equal(ibm.auto_close_date, pd.Timestamp("2015-01-03"))
        assert_equal(pl.start_date, pd.Timestamp("2013-03-01"))
        assert_equal(pl.end_date, pd.Timestamp("2013-03-04"))
        assert_equal(zzz.start_date, pd.Timestamp("2014-06-02"))
        assert_equal(zzz.end_date, pd.Timestamp("2014-06-03"))

        daily_reader = bundle.equity_daily_bar_reader
        assert_equal(
            daily_reader.get_value(2, pd.Timestamp("2013-03-04"), "close"), 11.5
        )
        expected_aapl = pd.read_csv(
            test_resource_path("csvdir_samples", "csvdir", "daily", "AAPL.csv.gz"),
            parse_dates=["date"],
            index_col="date",
        )
        assert_equal(
            daily_reader.get_value(0, self.asset_end, "close"),
            expected_aapl.loc[self.asset_end, "close"],
        )

        minute_reader = bundle.equity_minute_bar_reader
        assert_equal(
            minute_reader.get_value(
                1, pd.Timestamp("2015-01-02 21:00", tz="UTC"), "close"
            ),
            161.5,
        )
        assert_equal(
            minute_reader.get_value(
                3, pd.Timestamp("2014-06-02 13:31", tz="UTC"), "volume"
            ),
            300,
        )

        # The adjustments are those in the daily files, written once.
        adjustments = bundle.adjustment_reader.unpack_db_to_component_dfs()
        expected_split_count = sum(
            (
                pd.read_csv(
                    test_resource_path(
                        "csvdir_samples", "csvdir", "daily", symbol + ".csv.gz"
                    )
                )["split"]
                != 1.0
            ).sum()
            for symbol in ("AAPL", "IBM")
        )
        splits = adjustments["splits"]
        assert_equal(len(splits), expected_split_count)
        assert not (
            (splits["sid"] == 1)
            & (splits["effective_date"] == pd.Timestamp("2015-01-02"))
        ).any()
