import json
import re
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest
import requests
import responses

from zipline.data.bundles import bundles, ingest, load, register, unregister
from zipline.data.bundles.massive import API_URL, massive_equities
from zipline.data.bundles.vendor import (
    RateLimitedSession,
    SidMap,
    completed_sessions,
)
from zipline.testing.fixtures import WithInstanceTmpDir, ZiplineTestCase
from zipline.testing.predicates import assert_equal
from zipline.utils.calendar_utils import get_calendar

T = pd.Timestamp
SESSIONS = pd.DatetimeIndex(
    ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]
).as_unit("ns")

# Listings, as Massive's reference data would describe them:
# - AAA, listed throughout;
# - FB, renamed META on 2020-01-06 (one composite FIGI);
# - REU, delisted on 2020-01-03 and reused by another company;
# - an ETF;
# - a warrant, whose type isn't ingested.
TICKERS = [
    {"ticker": "AAA", "name": "Triple A", "type": "CS", "active": True,
     "composite_figi": "FIGI-AAA", "primary_exchange": "XNYS"},
    {"ticker": "FB", "name": "Facebook", "type": "CS", "active": False,
     "composite_figi": "FIGI-META", "primary_exchange": "XNAS",
     "delisted_utc": "2020-01-06T05:00:00Z"},
    {"ticker": "META", "name": "Meta Platforms", "type": "CS", "active": True,
     "composite_figi": "FIGI-META", "primary_exchange": "XNAS"},
    {"ticker": "REU", "name": "Old Reuse", "type": "CS", "active": False,
     "composite_figi": "FIGI-OLD", "delisted_utc": "2020-01-03T05:00:00Z"},
    {"ticker": "REU", "name": "New Reuse", "type": "CS", "active": True,
     "composite_figi": "FIGI-NEW", "primary_exchange": "XNYS"},
    {"ticker": "SPY", "name": "SPDR S&P 500", "type": "ETF", "active": True,
     "composite_figi": "FIGI-SPY", "primary_exchange": "ARCX"},
    {"ticker": "WWW", "name": "A Warrant", "type": "WARRANT", "active": True,
     "composite_figi": "FIGI-WWW", "primary_exchange": "XNYS"},
]  # fmt: skip


def bar(ticker, price, volume=1000):
    return {
        "T": ticker, "o": price, "h": price + 1, "l": price - 1, "c": price,
        "v": volume, "vw": price, "t": 0, "n": 10,
    }  # fmt: skip


BARS = {
    "2020-01-02": [bar("AAA", 100), bar("FB", 200), bar("REU", 10), bar("SPY", 300),
                   bar("WWW", 1)],
    "2020-01-03": [bar("AAA", 101), bar("FB", 201), bar("REU", 11), bar("SPY", 301)],
    "2020-01-06": [bar("AAA", 102), bar("META", 202), bar("SPY", 302),
                   # The old ticker's last print on the day of the change.
                   bar("FB", 202, volume=5)],
    # AAA splits 2-for-1.
    "2020-01-07": [bar("AAA", 51.5), bar("META", 203), bar("REU", 20),
                   bar("SPY", 303)],
    "2020-01-08": [bar("AAA", 52), bar("META", 204), bar("REU", 21), bar("SPY", 304)],
}  # fmt: skip

SPLITS = [
    {"ticker": "AAA", "execution_date": "2020-01-07", "split_from": 1,
     "split_to": 2, "adjustment_type": "forward_split"},
]  # fmt: skip

DIVIDENDS = [
    {"ticker": "META", "cash_amount": 0.5, "currency": "USD",
     "ex_dividend_date": "2020-01-07", "record_date": "2020-01-08",
     "declaration_date": "2019-12-20", "pay_date": "2020-01-15"},
    {"ticker": "SPY", "cash_amount": 0.4, "currency": "CAD",
     "ex_dividend_date": "2020-01-07", "record_date": "2020-01-08",
     "declaration_date": "2019-12-20", "pay_date": "2020-01-15"},
    # Before the ticker was reused, so it belongs to the old REU.
    {"ticker": "REU", "cash_amount": 0.1, "currency": "USD",
     "ex_dividend_date": "2020-01-03", "record_date": None,
     "declaration_date": None, "pay_date": None},
]  # fmt: skip


class MassiveAPI:
    """A fake Massive API serving the data above."""

    def __init__(self, rsps):
        self.grouped_requests = []
        # Sessions older than the plan's history.
        self.refused = set()
        rsps.add_callback(
            responses.GET,
            re.compile(
                re.escape(API_URL) + r"/v2/aggs/grouped/locale/us/market/stocks/"
            ),
            callback=self.grouped,
        )
        rsps.add_callback(
            responses.GET,
            re.compile(re.escape(API_URL) + r"/v3/reference/tickers"),
            callback=self.tickers,
        )
        rsps.add_callback(
            responses.GET,
            re.compile(re.escape(API_URL) + r"/stocks/v1/splits"),
            callback=self.dated(SPLITS, "execution_date"),
        )
        rsps.add_callback(
            responses.GET,
            re.compile(re.escape(API_URL) + r"/stocks/v1/dividends"),
            callback=self.dated(DIVIDENDS, "ex_dividend_date"),
        )

    @staticmethod
    def params(request):
        return {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}

    @staticmethod
    def ok(body):
        return 200, {}, json.dumps(body)

    def grouped(self, request):
        assert request.headers["Authorization"] == "Bearer secret"
        assert self.params(request) == {"adjusted": "false"}
        day = urlparse(request.url).path.rsplit("/", 1)[-1]
        self.grouped_requests.append(day)
        if day in self.refused:
            body = {"status": "NOT_AUTHORIZED", "message": "Upgrade your plan."}
            return 403, {}, json.dumps(body)
        return self.ok({"status": "OK", "results": BARS.get(day, [])})

    def tickers(self, request):
        params = self.params(request)
        page = int(params.pop("cursor", 0))
        active = params["active"] == "true"
        matches = [
            record
            for record in TICKERS
            if record["type"] == params["type"] and record["active"] == active
        ]
        # One listing per page, to exercise pagination.
        body = {"status": "OK", "results": matches[page : page + 1]}
        if page + 1 < len(matches):
            query = params | {"cursor": page + 1}
            body["next_url"] = f"{API_URL}/v3/reference/tickers?" + "&".join(
                f"{k}={v}" for k, v in query.items()
            )
        return self.ok(body)

    def dated(self, records, column):
        def callback(request):
            params = self.params(request)
            start = params[f"{column}.gte"]
            end = params[f"{column}.lte"]
            results = [r for r in records if start <= r[column] <= end]
            return self.ok({"status": "OK", "results": results})

        return callback


class MassiveBundleTestCase(WithInstanceTmpDir, ZiplineTestCase):
    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.environ = {
            "ZIPLINE_ROOT": self.instance_tmpdir.getpath("root"),
            "MASSIVE_API_KEY": "secret",
        }
        self.rsps = self.enter_instance_context(
            responses.RequestsMock(assert_all_requests_are_fired=False)
        )
        self.api = MassiveAPI(self.rsps)

    def ingest(self, end):
        if "test-massive" in bundles:
            unregister("test-massive")
        else:
            self.add_instance_callback(lambda: unregister("test-massive"))
        register(
            "test-massive",
            massive_equities(start_session=SESSIONS[0], calls_per_minute=0),
            calendar_name="XNYS",
            end_session=end,
        )
        ingest("test-massive", environ=self.environ)
        bundle = load("test-massive", environ=self.environ)
        self.add_instance_callback(bundle.close)
        return bundle

    def test_ingest(self):
        bundle = self.ingest(SESSIONS[-1])
        finder = bundle.asset_finder

        # One sid per security, in sorted order of their FIGIs; the warrant
        # isn't ingested.
        aaa, meta, new_reu, old_reu, spy = finder.retrieve_all([0, 1, 2, 3, 4])
        assert_equal(len(finder.sids), 5)
        assert_equal(
            [a.asset_name for a in (aaa, meta, new_reu, old_reu, spy)],
            ["Triple A", "Meta Platforms", "New Reuse", "Old Reuse", "SPDR S&P 500"],
        )

        # The ticker change keeps one sid, and the reused ticker gets two.
        assert_equal(finder.lookup_symbol("FB", SESSIONS[0]), meta)
        assert_equal(finder.lookup_symbol("META", SESSIONS[-1]), meta)
        assert_equal(finder.lookup_symbol("REU", SESSIONS[0]), old_reu)
        assert_equal(finder.lookup_symbol("REU", SESSIONS[-1]), new_reu)

        # Lifetimes and exchanges.
        assert_equal((meta.start_date, meta.end_date), (SESSIONS[0], SESSIONS[-1]))
        assert_equal((old_reu.start_date, old_reu.end_date), (SESSIONS[0], SESSIONS[1]))
        assert_equal(old_reu.auto_close_date, T("2020-01-04"))
        assert_equal(
            (new_reu.start_date, new_reu.end_date), (SESSIONS[3], SESSIONS[-1])
        )
        # Exchanges are named by MIC, and use their own calendar if there is
        # one.
        assert_equal(
            [(a.exchange_full, a.exchange) for a in (aaa, meta, old_reu, spy)],
            [
                ("XNYS", "XNYS"),
                ("XNAS", "XNAS"),
                ("UNKNOWN", "XNYS"),
                ("ARCX", "ARCX"),
            ],
        )

        # Unadjusted bars; META's bar on the day of the change is its own,
        # not the old ticker's last print.
        closes = bundle.equity_daily_bar_reader.load_raw_arrays(
            ["close"], SESSIONS[0], SESSIONS[-1], [0, 1, 3]
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
        assert_equal(splits["sid"].tolist(), [0])
        assert_equal(splits["ratio"].tolist(), [0.5])
        dividends = adjustments["dividend_payouts"].sort_values("sid")
        # META's in USD, and the old REU's; not SPY's in CAD.
        assert_equal(dividends["sid"].tolist(), [1, 3])
        assert_equal(dividends["amount"].tolist(), [0.5, 0.1])

    def test_incremental(self):
        self.ingest(SESSIONS[2])
        assert_equal(
            self.api.grouped_requests, ["2020-01-02", "2020-01-03", "2020-01-06"]
        )

        bundle = self.ingest(SESSIONS[-1])
        # Only the new sessions are downloaded.
        assert_equal(self.api.grouped_requests[3:], ["2020-01-07", "2020-01-08"])
        # The new REU had no bars in the first ingestion. The sids given
        # then stay the same, and the new security gets the next one.
        finder = bundle.asset_finder
        assert_equal(finder.lookup_symbol("REU", SESSIONS[0]).sid, 2)
        assert_equal(finder.lookup_symbol("SPY", SESSIONS[0]).sid, 3)
        assert_equal(finder.lookup_symbol("REU", SESSIONS[-1]).sid, 4)

    def test_missing_api_key(self):
        del self.environ["MASSIVE_API_KEY"]
        with pytest.raises(ValueError, match="MASSIVE_API_KEY"):
            self.ingest(SESSIONS[-1])

    def test_plan_history(self):
        self.api.refused.add("2020-01-02")
        with pytest.raises(ValueError, match="set MASSIVE_START_DATE"):
            self.ingest(SESSIONS[-1])

    def test_unpublished_session(self):
        # Massive has nothing for the last session yet: it is ingested
        # without it, and downloaded at the next ingestion.
        missing = BARS.pop("2020-01-08")
        try:
            bundle = self.ingest(SESSIONS[-1])
        finally:
            BARS["2020-01-08"] = missing
        closes = bundle.equity_daily_bar_reader.load_raw_arrays(
            ["close"], SESSIONS[-2], SESSIONS[-1], [0]
        )[0]
        np.testing.assert_array_equal(closes, [[51.5], [np.nan]])
        self.ingest(SESSIONS[-1])
        assert_equal(self.api.grouped_requests.count("2020-01-08"), 2)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RateLimitedSessionTestCase(ZiplineTestCase):
    url = "https://api.example.com/data"

    def session(self, **kwargs):
        clock = FakeClock()
        session = RateLimitedSession(sleep=clock.sleep, clock=clock, **kwargs)
        self.add_instance_callback(session.close)
        return session, clock

    @responses.activate
    def test_spaces_requests(self):
        responses.get(self.url, json={"ok": True})
        session, clock = self.session(calls_per_minute=6)
        for _ in range(3):
            assert_equal(session.get_json(self.url), {"ok": True})
            clock.now += 1
        assert_equal(clock.sleeps, [9.0, 9.0])

    @responses.activate
    def test_retries(self):
        responses.get(self.url, status=429, headers={"Retry-After": "30"})
        responses.get(self.url, status=503)
        responses.get(self.url, body=requests.ConnectionError("reset"))
        responses.get(self.url, json={"ok": True})
        session, clock = self.session(backoff=1.0)
        assert_equal(session.get_json(self.url), {"ok": True})
        assert_equal(clock.sleeps, [30.0, 2.0, 4.0])

    @responses.activate
    def test_gives_up(self):
        responses.get(self.url, status=503)
        session, clock = self.session(retries=2, backoff=1.0)
        with pytest.raises(requests.HTTPError):
            session.get_json(self.url)
        assert_equal(len(responses.calls), 3)

    @responses.activate
    def test_client_error(self):
        responses.get(self.url, status=403)
        session, clock = self.session()
        with pytest.raises(requests.HTTPError):
            session.get_json(self.url)
        assert_equal(len(responses.calls), 1)


class SidMapTestCase(WithInstanceTmpDir, ZiplineTestCase):
    def test_assign(self):
        path = self.instance_tmpdir.getpath("sids.parquet")
        assert_equal(SidMap(path).assign(["b", "a"]).to_dict(), {"b": 1, "a": 0})
        # Kept across instances; new keys get the next sids.
        assert_equal(
            SidMap(path).assign(["c", "a", "0"]).to_dict(), {"c": 3, "a": 0, "0": 2}
        )


def test_completed_sessions():
    calendar = get_calendar("XNYS")
    # 2020-01-08 closes at 21:00 UTC.
    now = T("2020-01-09 00:30", tz="UTC")
    sessions = completed_sessions(
        calendar, SESSIONS[0], SESSIONS[-1], now, pd.Timedelta(hours=4)
    )
    assert_equal(sessions, SESSIONS[:-1])
    sessions = completed_sessions(
        calendar, SESSIONS[0], SESSIONS[-1], now, pd.Timedelta(hours=3)
    )
    assert_equal(sessions, SESSIONS)
