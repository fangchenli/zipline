import json
import re
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest
import responses

from zipline.data.bundles import bundles, ingest, load, register, unregister
from zipline.data.bundles.alpaca import DATA_URL, TRADING_URL, alpaca_equities
from zipline.errors import SymbolNotFound
from zipline.testing.fixtures import WithInstanceTmpDir, ZiplineTestCase
from zipline.testing.predicates import assert_equal

SESSIONS = pd.DatetimeIndex(
    ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]
).as_unit("ns")

# The assets listed today. OTCX trades over the counter, so isn't ingested.
ASSETS = [
    {"symbol": "AAA", "name": "Triple A", "exchange": "NYSE", "status": "active"},
    {"symbol": "META", "name": "Meta Platforms", "exchange": "NASDAQ",
     "status": "active"},
    {"symbol": "REU", "name": "New Reuse", "exchange": "NYSE", "status": "active"},
    {"symbol": "SPY", "name": "SPDR S&P 500", "exchange": "ARCA", "status": "active"},
    {"symbol": "OTCX", "name": "Over The Counter", "exchange": "OTC",
     "status": "active"},
]  # fmt: skip


def bar(price, volume=1000):
    return {"o": price, "h": price + 1, "l": price - 1, "c": price, "v": volume}


# Bars by ticker as traded (asof="-"), by session:
# - FB is renamed META on 2020-01-06;
# - REU is acquired on 2020-01-06, and the ticker reused from 2020-01-07;
# - TWT is acquired on 2020-01-08.
BARS = {
    "AAA": {"2020-01-02": bar(100), "2020-01-03": bar(101), "2020-01-06": bar(102),
            # A 2-for-1 split.
            "2020-01-07": bar(51.5), "2020-01-08": bar(52)},
    "FB": {"2020-01-02": bar(200), "2020-01-03": bar(201),
           # A last print under the old ticker on the day of the rename.
           "2020-01-06": bar(202, volume=5),
           # Stale bars with no volume after the rename.
           "2020-01-07": bar(202, volume=0), "2020-01-08": bar(202, volume=0)},
    "META": {"2020-01-06": bar(202), "2020-01-07": bar(203), "2020-01-08": bar(204)},
    "REU": {"2020-01-02": bar(10), "2020-01-03": bar(11),
            # A stale bar with no volume, which would otherwise start the new
            # REU's history early.
            "2020-01-06": bar(11, volume=0),
            "2020-01-07": bar(20), "2020-01-08": bar(21)},
    "SPY": {day: bar(300 + i) for i, day in enumerate(
        ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"])},
    "TWT": {"2020-01-02": bar(50), "2020-01-03": bar(51), "2020-01-06": bar(52),
            "2020-01-07": bar(53)},
    "OTCX": {"2020-01-02": bar(1)},
}  # fmt: skip

# Corporate actions by type, with the dates they were processed.
ACTIONS = {
    "name_changes": [
        {"old_symbol": "FB", "new_symbol": "META", "process_date": "2020-01-06"},
    ],
    "cash_mergers": [
        {"acquiree_symbol": "REU", "effective_date": "2020-01-06", "rate": 12,
         "process_date": "2020-01-06"},
        # A cash merger ends the security even without an acquirer ticker.
        {"acquiree_symbol": "TWT", "acquirer_symbol": "", "rate": 54,
         "effective_date": "2020-01-08", "process_date": "2020-01-08"},
    ],
    # An ETF reorganization, and a new holding company taking over AAA's
    # ticker, which continue the securities.
    "stock_mergers": [
        {"acquiree_symbol": "SPY", "acquirer_symbol": "SPY", "acquiree_rate": 1,
         "acquirer_rate": 1, "effective_date": "2020-01-07",
         "process_date": "2020-01-07"},
        {"acquiree_symbol": "AAA", "acquirer_symbol": "", "acquiree_rate": 1,
         "acquirer_rate": 1, "effective_date": "2020-01-06",
         "process_date": "2020-01-06"},
    ],
    # A ticker Alpaca has no bars for.
    "worthless_removals": [{"symbol": "BAD", "process_date": "2020-01-07"}],
    "forward_splits": [
        {"symbol": "AAA", "ex_date": "2020-01-07", "old_rate": 1, "new_rate": 2,
         "process_date": "2020-01-07"},
    ],
    "cash_dividends": [
        # On the first session, so left out.
        {"symbol": "AAA", "rate": 1.0, "ex_date": "2020-01-02",
         "record_date": "2020-01-03", "payable_date": "2020-01-15",
         "process_date": "2020-01-15"},
        {"symbol": "META", "rate": 0.5, "ex_date": "2020-01-07",
         "record_date": "2020-01-08", "payable_date": "2020-01-15",
         "process_date": "2020-01-15"},
        # A typo in the year, as in Alpaca's data; left out.
        {"symbol": "AAA", "rate": 9.0, "ex_date": "3026-01-07",
         "record_date": "2020-01-08", "payable_date": "2020-01-15",
         "process_date": "2020-01-15"},
        # Paid under the old ticker, by the security that was acquired.
        {"symbol": "REU", "rate": 0.1, "ex_date": "2020-01-03",
         "record_date": "2020-01-06", "payable_date": "2020-01-10",
         "process_date": "2020-01-10"},
    ],
}  # fmt: skip

TYPES = {
    "name_change": "name_changes",
    "cash_merger": "cash_mergers",
    "stock_merger": "stock_mergers",
    "stock_and_cash_merger": "stock_and_cash_mergers",
    "worthless_removal": "worthless_removals",
    "forward_split": "forward_splits",
    "reverse_split": "reverse_splits",
    "cash_dividend": "cash_dividends",
}


class AlpacaAPI:
    """A fake Alpaca API serving the data above."""

    page_size = 3

    def __init__(self, rsps):
        self.bar_requests = []
        self.action_requests = []
        # Tickers whose corporate actions Alpaca hasn't reported yet.
        self.unreported = set()
        rsps.add_callback(
            responses.GET, f"{TRADING_URL}/v2/assets", callback=self.assets
        )
        rsps.add_callback(
            responses.GET,
            re.compile(re.escape(f"{DATA_URL}/v2/stocks/bars")),
            callback=self.bars,
        )
        rsps.add_callback(
            responses.GET,
            re.compile(re.escape(f"{DATA_URL}/v1/corporate-actions")),
            callback=self.actions,
        )

    @staticmethod
    def params(request):
        assert request.headers["APCA-API-KEY-ID"] == "key"
        assert request.headers["APCA-API-SECRET-KEY"] == "secret"
        return {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}

    def page(self, params, items, body):
        start = int(params.get("page_token", 0))
        end = start + self.page_size
        body["next_page_token"] = str(end) if end < len(items) else None
        return items[start:end], body

    def assets(self, request):
        params = self.params(request)
        assert params == {"status": "active", "asset_class": "us_equity"}
        return 200, {}, json.dumps(ASSETS)

    def bars(self, request):
        params = self.params(request)
        assert (params["timeframe"], params["adjustment"], params["feed"]) == (
            "1Day",
            "raw",
            "sip",
        )
        assert params["asof"] == "-"
        symbols = params["symbols"].split(",")
        if "page_token" not in params:
            self.bar_requests.append((params["start"], symbols))
        for symbol in symbols:
            if symbol == "BAD":
                return 400, {}, json.dumps({"message": f"invalid symbol: {symbol}"})
        items = [
            (symbol, {"t": f"{day}T05:00:00Z", **values})
            for symbol in symbols
            for day, values in BARS.get(symbol, {}).items()
            if params["start"] <= day <= params["end"]
        ]
        page, body = self.page(params, items, {})
        bars = {}
        for symbol, values in page:
            bars.setdefault(symbol, []).append(values)
        body["bars"] = bars
        return 200, {}, json.dumps(body)

    def actions(self, request):
        params = self.params(request)
        if "page_token" not in params:
            self.action_requests.append(params["start"])
        kinds = [TYPES[t] for t in params["types"].split(",")]
        items = [
            (kind, action)
            for kind in kinds
            for action in ACTIONS.get(kind, [])
            if params["start"] <= action["process_date"] <= params["end"]
            and action.get("acquiree_symbol", action.get("symbol"))
            not in self.unreported
        ]
        page, body = self.page(params, items, {})
        actions = {kind: [] for kind in kinds}
        for kind, action in page:
            actions[kind].append(action)
        body["corporate_actions"] = actions
        return 200, {}, json.dumps(body)


class AlpacaBundleTestCase(WithInstanceTmpDir, ZiplineTestCase):
    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.environ = {
            "ZIPLINE_ROOT": self.instance_tmpdir.getpath("root"),
            "APCA_API_KEY_ID": "key",
            "APCA_API_SECRET_KEY": "secret",
        }
        self.rsps = self.enter_instance_context(
            responses.RequestsMock(assert_all_requests_are_fired=False)
        )
        self.api = AlpacaAPI(self.rsps)

    def ingest(self, end, **kwargs):
        if "test-alpaca" in bundles:
            unregister("test-alpaca")
        else:
            self.add_instance_callback(lambda: unregister("test-alpaca"))
        register(
            "test-alpaca",
            alpaca_equities(start_session=SESSIONS[0], calls_per_minute=0, **kwargs),
            calendar_name="XNYS",
            end_session=end,
        )
        ingest("test-alpaca", environ=self.environ)
        bundle = load("test-alpaca", environ=self.environ)
        self.add_instance_callback(bundle.close)
        return bundle

    def test_ingest(self):
        bundle = self.ingest(SESSIONS[-1])
        finder = bundle.asset_finder
        first, last = SESSIONS[0], SESSIONS[-1]

        def lookup(symbol, day):
            return finder.lookup_symbol(symbol, day)

        aaa, spy, twt = lookup("AAA", first), lookup("SPY", first), lookup("TWT", first)
        meta, old_reu, new_reu = (
            lookup("META", last),
            lookup("REU", first),
            lookup("REU", last),
        )
        # One sid per security; nothing over the counter.
        assert_equal(len(finder.sids), 6)
        assert_equal(len({a.sid for a in (aaa, spy, twt, meta, old_reu, new_reu)}), 6)

        # The rename keeps one sid, and the reused ticker gets two.
        assert_equal(lookup("FB", first), meta)
        assert_equal((meta.start_date, meta.end_date), (first, last))
        assert_equal((old_reu.start_date, old_reu.end_date), (first, SESSIONS[1]))
        assert_equal((new_reu.start_date, new_reu.end_date), (SESSIONS[3], last))
        assert_equal((twt.start_date, twt.end_date), (first, SESSIONS[3]))
        # The reorganizations don't end SPY or AAA.
        assert_equal((spy.start_date, spy.end_date), (first, last))
        assert_equal((aaa.start_date, aaa.end_date), (first, last))

        # Names and exchanges of listed assets; acquired ones have neither.
        assert_equal(
            [(a.asset_name, a.exchange_full) for a in (aaa, meta, new_reu, spy)],
            [
                ("Triple A", "XNYS"),
                ("Meta Platforms", "XNAS"),
                ("New Reuse", "XNYS"),
                ("SPDR S&P 500", "ARCX"),
            ],
        )
        assert_equal((old_reu.asset_name, old_reu.exchange_full), ("", "UNKNOWN"))

        closes = bundle.equity_daily_bar_reader.load_raw_arrays(
            ["close"], first, last, [aaa.sid, meta.sid, old_reu.sid]
        )[0]
        np.testing.assert_array_equal(
            closes,
            [
                [100, 200, 10],
                [101, 201, 11],
                [102, 202, np.nan],
                [51.5, 203, np.nan],
                [52, 204, np.nan],
            ],
        )

        adjustments = bundle.adjustment_reader.unpack_db_to_component_dfs()
        splits = adjustments["splits"]
        assert_equal(splits["sid"].tolist(), [aaa.sid])
        assert_equal(splits["ratio"].tolist(), [0.5])
        dividends = adjustments["dividend_payouts"]
        assert_equal(
            dict(zip(dividends["sid"], dividends["amount"])),
            {meta.sid: 0.5, old_reu.sid: 0.1},
        )

    def test_incremental(self):
        def sids(bundle):
            return {
                symbol: bundle.asset_finder.lookup_symbol(symbol, SESSIONS[0]).sid
                for symbol in ("AAA", "FB", "REU", "SPY", "TWT")
            }

        before = sids(self.ingest(SESSIONS[2]))
        assert_equal({start for start, _ in self.api.bar_requests}, {"2020-01-02"})
        del self.api.bar_requests[:]
        # Corporate actions of months long past are cached.
        assert_equal(self.api.action_requests[0], "2020-01-01")
        del self.api.action_requests[:]

        bundle = self.ingest(SESSIONS[-1])
        # Only the new sessions are downloaded.
        assert_equal({start for start, _ in self.api.bar_requests}, {"2020-01-07"})
        assert "2020-01-01" not in self.api.action_requests
        # Sids stay the same, and the new security gets the next one.
        assert_equal(sids(bundle), before)
        new_reu = bundle.asset_finder.lookup_symbol("REU", SESSIONS[-1])
        assert_equal(new_reu.sid, max(before.values()) + 1)

    def test_late_corporate_action(self):
        # Alpaca reports TWT's acquisition only after the first ingestion, so
        # its bars are downloaded for the sessions it missed. Its month is
        # checked again for late actions.
        refresh = pd.Timestamp.now() - SESSIONS[0]
        self.api.unreported.add("TWT")
        bundle = self.ingest(SESSIONS[2], refresh=refresh)
        with pytest.raises(SymbolNotFound):
            bundle.asset_finder.lookup_symbol("TWT", SESSIONS[0])
        del self.api.bar_requests[:]

        self.api.unreported.clear()
        bundle = self.ingest(SESSIONS[-1], refresh=refresh)
        twt = bundle.asset_finder.lookup_symbol("TWT", SESSIONS[0])
        assert_equal((twt.start_date, twt.end_date), (SESSIONS[0], SESSIONS[3]))
        # TWT is downloaded from the start, the rest only for the new
        # sessions; tickers Alpaca doesn't know aren't asked for again.
        requests = sorted(
            (start, tuple(symbols)) for start, symbols in self.api.bar_requests
        )
        assert_equal(requests[0], ("2020-01-02", ("TWT",)))
        assert all(start == "2020-01-07" for start, _ in requests[1:])
        assert not any("BAD" in symbols for _, symbols in requests)

    def test_missing_keys(self):
        del self.environ["APCA_API_SECRET_KEY"]
        with pytest.raises(ValueError, match="APCA_API_SECRET_KEY"):
            self.ingest(SESSIONS[-1])
