"""US equities from Massive (formerly Polygon.io).

The ``massive`` bundle downloads, with the user's API key:

- unadjusted daily bars for the whole US market, one request per session
  (Massive's "daily market summary"), cached so that later ingestions only
  download new sessions;
- reference data, to tell which security a ticker meant on each session:
  the listings as of the first and last sessions and the first of each month
  in between, cached, which show tickers that were later changed (Massive
  lists FB as Meta's ticker in 2021, but not among delisted tickers), and the
  delisted listings, for when securities were delisted. Securities are
  identified by their composite FIGI and issuer, so a ticker change (e.g. FB
  to META) keeps one sid, and a reused ticker gets a new one;
- splits and cash dividends.

Massive's free plan allows 5 requests a minute and 2 years of history; paid
plans allow more requests and history. Its terms allow individual use only,
so the downloaded data stays on the user's machine.
"""

import logging
import os

import networkx as nx
import numpy as np
import pandas as pd
import requests

from zipline.utils import paths as pth
from zipline.utils.cli import maybe_show_progress

from . import core as bundles
from .vendor import (
    DailyCache,
    RateLimitedSession,
    SidMap,
    completed_sessions,
    equities_frame,
    exchanges_frame,
    parse_dates,
)

log = logging.getLogger(__name__)

API_URL = "https://api.massive.com"

#: Security types ingested by default: common stock, ADRs of common stock and
#: ETFs. See Massive's ``/v3/reference/tickers/types`` for the others.
DEFAULT_TYPES = ("CS", "ADRC", "ETF")

#: The free plan's rate limit and history. The history is counted back from
#: the current time, so by default the bundle starts a week later than that.
FREE_PLAN_CALLS_PER_MINUTE = 5
FREE_PLAN_HISTORY = pd.DateOffset(years=2)
FREE_PLAN_MARGIN = pd.Timedelta(days=7)

#: How long after a session's close its daily bars are taken to be final.
SETTLE_DELAY = pd.Timedelta(hours=4)

CALENDAR_NAME = "XNYS"

PRICE_COLUMNS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}


def massive_equities(
    api_key=None,
    start_session=None,
    calls_per_minute=None,
    types=DEFAULT_TYPES,
):
    """An ingest function for US equities from Massive.

    Arguments left as None are read from the environment given to
    ``ingest``, and otherwise default to what Massive's free plan allows.

    Parameters
    ----------
    api_key : str, optional
        The Massive API key. Defaults to ``MASSIVE_API_KEY``.
    start_session : pd.Timestamp, optional
        The first session to ingest. Defaults to ``MASSIVE_START_DATE``, or
        else two years ago, the free plan's history.
    calls_per_minute : float, optional
        The plan's rate limit. Defaults to ``MASSIVE_CALLS_PER_MINUTE``, or
        else 5, the free plan's; 0 means unlimited, as on paid plans.
    types : iterable[str], optional
        The Massive security types to ingest.

    Returns
    -------
    ingest : callable
        The ingest function, to pass to :func:`zipline.data.bundles.register`
        with ``calendar_name="XNYS"``.
    """

    def ingest(
        environ,
        asset_db_writer,
        minute_bar_writer,
        daily_bar_writer,
        adjustment_writer,
        calendar,
        first_session,
        last_session,
        cache,
        show_progress,
        output_dir,
    ):
        key = api_key or environ.get("MASSIVE_API_KEY")
        if not key:
            raise ValueError(
                "Please set your MASSIVE_API_KEY environment variable and retry."
                " Get a free API key at https://massive.com."
            )
        now = pd.Timestamp.now(tz="UTC")
        start = start_session
        if start is None and environ.get("MASSIVE_START_DATE"):
            start = pd.Timestamp(environ["MASSIVE_START_DATE"])
        if start is None:
            start = (
                now.tz_localize(None).normalize() - FREE_PLAN_HISTORY + FREE_PLAN_MARGIN
            )
        start = max(start, first_session)
        rate = calls_per_minute
        if rate is None:
            rate = float(
                environ.get("MASSIVE_CALLS_PER_MINUTE", FREE_PLAN_CALLS_PER_MINUTE)
            )

        session = RateLimitedSession(
            calls_per_minute=rate or None,
            headers={"Authorization": f"Bearer {key}"},
        )
        try:
            _ingest(
                session,
                pth.cache_path(["massive"], environ=environ),
                asset_db_writer,
                daily_bar_writer,
                adjustment_writer,
                calendar,
                completed_sessions(calendar, start, last_session, now, SETTLE_DELAY),
                now.tz_localize(None).normalize(),
                types,
                show_progress,
            )
        finally:
            session.close()

    return ingest


def _ingest(
    session,
    cache_dir,
    asset_db_writer,
    daily_bar_writer,
    adjustment_writer,
    calendar,
    sessions,
    today,
    types,
    show_progress,
):
    if not len(sessions):
        raise ValueError("There are no completed sessions to ingest.")
    bar_cache = DailyCache(os.path.join(cache_dir, "daily"))
    _download_bars(session, bar_cache, bar_cache.missing(sessions), show_progress)
    sessions = sessions.difference(bar_cache.missing(sessions))
    if not len(sessions):
        raise ValueError("Massive has no bars for any session to ingest yet.")

    # Listings as of the first of each month, and of the first and last
    # sessions, so that every bar is between two of them.
    snapshot_dates = (
        pd.date_range(sessions[0], sessions[-1], freq="MS")
        .union(sessions[[0, -1]])
        .as_unit("ns")
    )
    tickers = _tickers(
        session,
        DailyCache(os.path.join(cache_dir, "tickers")),
        types,
        snapshot_dates[snapshot_dates < today],
        show_progress,
    )
    tickers["security"] = _securities(tickers)
    bars = bar_cache.get(sessions)
    bars["security"] = _resolve(tickers, bars["ticker"], bars["date"])
    unknown = bars["security"].isna()
    if unknown.any():
        log.info(
            "Skipping %d tickers that aren't securities of types %s.",
            bars.loc[unknown, "ticker"].nunique(),
            ", ".join(types),
        )
        bars = bars[~unknown]
    traded = tickers[tickers["security"].isin(bars["security"])]
    sids = SidMap(os.path.join(cache_dir, "sids.parquet")).assign(
        traded.drop_duplicates("key").set_index("key")["security"]
    )
    bars["sid"] = sids.loc[bars["security"]].to_numpy()
    # If a security traded under two tickers on one session, e.g. around a
    # ticker change, keep the busier one.
    bars = bars.sort_values("volume").drop_duplicates(["sid", "date"], keep="last")
    bars = bars.rename(columns={"ticker": "symbol"})

    # The latest listings of each security name it and its exchange;
    # delisted listings often have no exchange.
    info = (
        traded.sort_values("seen")
        .groupby("security")[["name", "exchange"]]
        .last()
        .rename(columns={"name": "asset_name"})
        .fillna({"exchange": "UNKNOWN"})
    )
    info.index = sids.loc[info.index].to_numpy()
    equities = equities_frame(bars, info)
    asset_db_writer.write(
        equities=equities,
        exchanges=exchanges_frame(equities["exchange"], CALENDAR_NAME, "US"),
    )

    daily_bar_writer.write(
        (
            (sid, frame.set_index("date")[list(PRICE_COLUMNS.values())])
            for sid, frame in bars.sort_values(["sid", "date"]).groupby("sid")
        ),
        assets=set(sids),
        show_progress=show_progress,
    )

    # Splits and dividends on the first session only affect earlier prices
    # and holdings, which the bundle doesn't have, so they are left out.
    start, end = sessions[0], sessions[-1]
    adjustment_writer.write(
        splits=_splits(session, tickers, sids, start, end),
        dividends=_dividends(session, tickers, sids, start, end),
    )


def _download_bars(session, bar_cache, sessions, show_progress):
    if not len(sessions):
        return
    log.info("Downloading daily bars for %d sessions.", len(sessions))
    with maybe_show_progress(
        sessions,
        show_progress,
        label="Downloading Massive daily bars:",
    ) as it:
        for day in it:
            try:
                data = session.get_json(
                    f"{API_URL}/v2/aggs/grouped/locale/us/market/stocks/{day:%Y-%m-%d}",
                    params={"adjusted": "false"},
                )
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 403:
                    raise
                raise ValueError(
                    f"Massive refused the bars for {day.date()}: "
                    f"{exc.response.text}\nYour plan may not include data that"
                    " old; set MASSIVE_START_DATE to a later date."
                ) from exc
            results = data.get("results") or []
            if not results:
                # Not published yet; try again at the next ingestion.
                log.warning("Massive has no bars for %s yet.", day.date())
                continue
            bars = pd.DataFrame(results)
            bar_cache.put(
                day,
                bars[["T", *PRICE_COLUMNS]].rename(
                    columns={"T": "ticker", **PRICE_COLUMNS}
                ),
            )


def _paginate(session, url, params):
    """All the results of a paginated Massive endpoint."""
    data = session.get_json(url, params)
    results = list(data.get("results") or [])
    while data.get("next_url"):
        data = session.get_json(data["next_url"])
        results.extend(data.get("results") or [])
    return results


def _tickers(session, snapshot_cache, types, snapshot_dates, show_progress):
    """Listings of securities of one of ``types``: as of ``snapshot_dates``,
    which are cached, and delisted ones.

    Returns
    -------
    tickers : pd.DataFrame
        The columns of :func:`_listings`, and ``seen``: when the ticker was
        known to mean the listed security (the snapshot date, or the delisting
        date).
    """

    def download(**params):
        records = []
        for type_ in types:
            records.extend(
                _paginate(
                    session,
                    f"{API_URL}/v3/reference/tickers",
                    {"market": "stocks", "type": type_, "limit": 1000, **params},
                )
            )
        return _listings(records)

    missing = snapshot_cache.missing(snapshot_dates)
    if len(missing):
        log.info("Downloading ticker listings for %d dates.", len(missing))
    with maybe_show_progress(
        missing,
        show_progress,
        label="Downloading Massive ticker listings:",
    ) as it:
        for day in it:
            listings = download(date=f"{day:%Y-%m-%d}", active="true")
            snapshot_cache.put(day, listings.drop(columns="delisted"))
    snapshots = snapshot_cache.get(snapshot_dates).rename(columns={"date": "seen"})

    delisted = download(active="false")
    delisted = delisted.assign(seen=delisted.pop("delisted"))
    tickers = pd.concat([snapshots, delisted], ignore_index=True)
    tickers["seen"] = tickers["seen"].astype("datetime64[ns]")
    return tickers.dropna(subset=["ticker", "seen"])


def _listings(records):
    """Massive ticker records as a DataFrame with columns ``ticker``, ``key``
    (an identifier of the security: its FIGI, or else its ticker and issuer),
    ``figi``, ``share_class_figi``, ``cik``, ``name``, ``exchange`` and
    ``delisted``.
    """
    records = pd.DataFrame(records).reindex(
        columns=[
            "ticker",
            "name",
            "cik",
            "primary_exchange",
            "composite_figi",
            "share_class_figi",
            "delisted_utc",
            "last_updated_utc",
        ]
    )

    def dates(column):
        return parse_dates(records[column], utc=True)

    # Securities without a FIGI are told apart by their issuer, or else name.
    fallback = records["ticker"] + ":" + records["cik"].fillna(records["name"])
    return pd.DataFrame(
        {
            "ticker": records["ticker"],
            "key": records["composite_figi"]
            .fillna(records["share_class_figi"])
            .fillna(fallback),
            "figi": records["composite_figi"],
            "share_class_figi": records["share_class_figi"],
            "cik": records["cik"],
            "name": records["name"],
            "exchange": records["primary_exchange"],
            # A delisted listing without a delisting date ended when it was
            # last updated.
            "delisted": dates("delisted_utc")
            .fillna(dates("last_updated_utc"))
            .dt.normalize(),
        }
    )


def _securities(tickers):
    """The security each listing is of, named by one of its keys.

    Massive's identifiers aren't permanent: a security can get a new FIGI
    (Oneok's changed in 2025) or issuer CIK, and delisted listings often have
    no FIGI. So listings are linked into securities: those with the same key,
    and consecutive listings of the same ticker with the same name, or else
    with the same issuer (CIK) or, without issuers to compare, no different
    FIGIs. A ticker reused by another company gets a new security.
    """
    graph = nx.Graph()
    graph.add_nodes_from(tickers["key"])
    has_both = tickers["figi"].notna() & tickers["share_class_figi"].notna()
    graph.add_edges_from(
        zip(tickers.loc[has_both, "figi"], tickers.loc[has_both, "share_class_figi"])
    )
    ordered = tickers.sort_values(["ticker", "seen"])
    previous = ordered.groupby("ticker").shift()
    both_cik = ordered["cik"].notna() & previous["cik"].notna()
    both_figi = ordered["figi"].notna() & previous["figi"].notna()
    same = previous["key"].notna() & (
        (ordered["name"] == previous["name"])
        | np.where(
            both_cik,
            ordered["cik"] == previous["cik"],
            ~both_figi | (ordered["figi"] == previous["figi"]),
        )
    )
    graph.add_edges_from(zip(ordered.loc[same, "key"], previous.loc[same, "key"]))
    security = {}
    for component in nx.connected_components(graph):
        name = min(component)
        security.update(dict.fromkeys(component, name))
    return tickers["key"].map(security)


def _resolve(tickers, symbols, dates):
    """The security each of ``symbols`` meant on the matching date.

    That is the security the ticker was next seen to mean, or else, for a
    ticker that was later changed, the one it was last seen to mean. NaN if
    the ticker was never seen.
    """
    query = pd.DataFrame(
        {
            "ticker": pd.Series(symbols).to_numpy(),
            "date": pd.DatetimeIndex(dates).as_unit("ns"),
            "position": range(len(symbols)),
        }
    ).sort_values("date")
    listings = tickers[["ticker", "seen", "security"]].sort_values("seen")

    def match(direction):
        return (
            pd.merge_asof(
                query,
                listings,
                left_on="date",
                right_on="seen",
                by="ticker",
                direction=direction,
            )
            .set_index("position")["security"]
            .sort_index()
        )

    return match("forward").fillna(match("backward")).to_numpy()


def _with_sids(frame, tickers, sids, date_column):
    """``frame`` with a ``sid`` column, keeping the rows for securities with
    bars.
    """
    securities = pd.Series(
        _resolve(tickers, frame["ticker"], frame[date_column]), index=frame.index
    )
    frame = frame.assign(sid=securities.map(sids))
    return frame[frame["sid"].notna()].astype({"sid": "int64"})


def _splits(session, tickers, sids, start, end):
    results = _paginate(
        session,
        f"{API_URL}/stocks/v1/splits",
        {
            "execution_date.gt": f"{start:%Y-%m-%d}",
            "execution_date.lte": f"{end:%Y-%m-%d}",
            "limit": 5000,
        },
    )
    if not results:
        return None
    splits = pd.DataFrame(results)
    splits["effective_date"] = parse_dates(splits["execution_date"])
    splits = _with_sids(splits, tickers, sids, "effective_date")
    # The price multiplier for bars before the split; 0.5 for a 2-for-1
    # split.
    splits["ratio"] = splits["split_from"] / splits["split_to"]
    return splits[["sid", "ratio", "effective_date"]]


def _dividends(session, tickers, sids, start, end):
    results = _paginate(
        session,
        f"{API_URL}/stocks/v1/dividends",
        {
            "ex_dividend_date.gt": f"{start:%Y-%m-%d}",
            "ex_dividend_date.lte": f"{end:%Y-%m-%d}",
            "limit": 5000,
        },
    )
    if not results:
        return None
    dividends = pd.DataFrame(results).reindex(
        columns=[
            "ticker",
            "cash_amount",
            "currency",
            "ex_dividend_date",
            "record_date",
            "declaration_date",
            "pay_date",
        ]
    )
    other_currency = dividends["currency"].fillna("USD") != "USD"
    if other_currency.any():
        log.info("Skipping %d dividends not paid in USD.", int(other_currency.sum()))
        dividends = dividends[~other_currency]
    dividends = pd.DataFrame(
        {
            "ticker": dividends["ticker"],
            "amount": dividends["cash_amount"].astype("float64"),
            "ex_date": parse_dates(dividends["ex_dividend_date"]),
            "record_date": parse_dates(dividends["record_date"]),
            "declared_date": parse_dates(dividends["declaration_date"]),
            "pay_date": parse_dates(dividends["pay_date"]),
        }
    )
    dividends = _with_sids(dividends, tickers, sids, "ex_date")
    return dividends[
        ["sid", "amount", "ex_date", "record_date", "declared_date", "pay_date"]
    ]


bundles.register("massive", massive_equities(), calendar_name=CALENDAR_NAME)
