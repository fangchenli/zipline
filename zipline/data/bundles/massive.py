"""US equities from Massive (formerly Polygon.io).

The ``massive`` bundle downloads, with the user's API key:

- unadjusted daily bars for the whole US market, one request per session
  (Massive's "daily market summary"), cached so that later ingestions only
  download new sessions;
- reference data for current and delisted tickers, to tell which security a
  ticker meant on each session. Securities are identified by their composite
  FIGI, so a ticker change (e.g. FB to META) keeps one sid, and a reused
  ticker gets a new one;
- splits and cash dividends.

Massive's free plan allows 5 requests a minute and 2 years of history; paid
plans allow more requests and history. Its terms allow individual use only,
so the downloaded data stays on the user's machine.
"""

import logging
import os

import pandas as pd
import requests

from zipline.utils import paths as pth
from zipline.utils.cli import maybe_show_progress

from . import core as bundles
from .vendor import (
    DailyBarCache,
    RateLimitedSession,
    SidMap,
    completed_sessions,
    equities_frame,
    exchanges_frame,
)

log = logging.getLogger(__name__)

API_URL = "https://api.massive.com"

#: Security types ingested by default: common stock, ADRs of common stock and
#: ETFs. See Massive's ``/v3/reference/tickers/types`` for the others.
DEFAULT_TYPES = ("CS", "ADRC", "ETF")

#: The free plan's rate limit and history.
FREE_PLAN_CALLS_PER_MINUTE = 5
FREE_PLAN_HISTORY = pd.DateOffset(years=2)

#: How long after a session's close its daily bars are taken to be final.
SETTLE_DELAY = pd.Timedelta(hours=4)

CALENDAR_NAME = "XNYS"

PRICE_COLUMNS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}

# The end of time for tickers that are still listed.
_ACTIVE = pd.Timestamp.max.normalize()


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
            start = now.tz_localize(None).normalize() - FREE_PLAN_HISTORY
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
    types,
    show_progress,
):
    if not len(sessions):
        raise ValueError("There are no completed sessions to ingest.")
    bar_cache = DailyBarCache(os.path.join(cache_dir, "daily"))
    _download_bars(session, bar_cache, bar_cache.missing(sessions), show_progress)
    sessions = sessions.difference(bar_cache.missing(sessions))
    if not len(sessions):
        raise ValueError("Massive has no bars for any session to ingest yet.")

    tickers = _tickers(session, types)
    bars = bar_cache.get(sessions)
    bars["key"] = _resolve(tickers, bars["ticker"], bars["date"])
    unknown = bars["key"].isna()
    if unknown.any():
        log.info(
            "Skipping %d tickers that aren't securities of types %s.",
            bars.loc[unknown, "ticker"].nunique(),
            ", ".join(types),
        )
        bars = bars[~unknown]
    sids = SidMap(os.path.join(cache_dir, "sids.parquet")).assign(bars["key"].unique())
    bars["sid"] = sids.loc[bars["key"]].to_numpy()
    # If a security traded under two tickers on one session, e.g. around a
    # ticker change, keep the busier one.
    bars = bars.sort_values("volume").drop_duplicates(["sid", "date"], keep="last")
    bars = bars.rename(columns={"ticker": "symbol"})

    # The latest listing of each security names it.
    latest = (
        tickers[tickers["key"].isin(sids.index)]
        .sort_values("until")
        .drop_duplicates("key", keep="last")
    )
    info = pd.DataFrame(
        {
            "asset_name": latest["name"].to_numpy(),
            "exchange": latest["exchange"].to_numpy(),
        },
        index=sids.loc[latest["key"]].to_numpy(),
    )
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


def _tickers(session, types):
    """Every listing of a security of one of ``types``, current or delisted.

    Returns
    -------
    tickers : pd.DataFrame
        Columns ``ticker``, ``key`` (the security), ``until`` (the date the
        listing ended, or a date in the far future if it hasn't), ``name`` and
        ``exchange``.
    """
    records = []
    for type_ in types:
        for active in ("true", "false"):
            records.extend(
                _paginate(
                    session,
                    f"{API_URL}/v3/reference/tickers",
                    {
                        "market": "stocks",
                        "type": type_,
                        "active": active,
                        "limit": 1000,
                    },
                )
            )
    columns = [
        "ticker",
        "name",
        "primary_exchange",
        "active",
        "composite_figi",
        "share_class_figi",
        "delisted_utc",
        "last_updated_utc",
    ]
    tickers = pd.DataFrame(records).reindex(columns=columns)

    def dates(column):
        return pd.to_datetime(tickers[column], utc=True).dt.tz_localize(None)

    # A delisted listing without a delisting date ended when it was last
    # updated.
    ended = dates("delisted_utc").fillna(dates("last_updated_utc")).dt.normalize()
    until = ended.where(~tickers["active"].fillna(False).astype(bool), _ACTIVE)
    fallback = tickers["ticker"] + ":" + until.dt.strftime("%Y-%m-%d")
    key = tickers["composite_figi"].fillna(tickers["share_class_figi"])
    tickers = pd.DataFrame(
        {
            "ticker": tickers["ticker"],
            "key": key.fillna(fallback),
            "until": until.astype("datetime64[ns]"),
            "name": tickers["name"],
            "exchange": tickers["primary_exchange"].fillna("UNKNOWN"),
        }
    )
    return tickers.dropna(subset=["ticker", "until"]).drop_duplicates(
        ["ticker", "until"]
    )


def _resolve(tickers, symbols, dates):
    """The security each of ``symbols`` meant on the matching date: the
    first listing of that ticker that hadn't ended by then. NaN where there
    is none.
    """
    query = pd.DataFrame(
        {
            "ticker": pd.Series(symbols).to_numpy(),
            "date": pd.DatetimeIndex(dates).as_unit("ns"),
            "position": range(len(symbols)),
        }
    )
    listings = tickers[["ticker", "until", "key"]].sort_values("until")
    matched = pd.merge_asof(
        query.sort_values("date"),
        listings,
        left_on="date",
        right_on="until",
        by="ticker",
        direction="forward",
    )
    return matched.set_index("position")["key"].sort_index().to_numpy()


def _with_sids(frame, tickers, sids, date_column):
    """``frame`` with a ``sid`` column, keeping the rows for securities with
    bars.
    """
    keys = pd.Series(
        _resolve(tickers, frame["ticker"], frame[date_column]), index=frame.index
    )
    frame = frame.assign(sid=keys.map(sids))
    return frame[frame["sid"].notna()].astype({"sid": "int64"})


def _splits(session, tickers, sids, start, end):
    results = _paginate(
        session,
        f"{API_URL}/stocks/v1/splits",
        {
            "execution_date.gte": f"{start:%Y-%m-%d}",
            "execution_date.lte": f"{end:%Y-%m-%d}",
            "limit": 5000,
        },
    )
    if not results:
        return None
    splits = pd.DataFrame(results)
    splits["effective_date"] = pd.to_datetime(splits["execution_date"])
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
            "ex_dividend_date.gte": f"{start:%Y-%m-%d}",
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
            "ex_date": pd.to_datetime(dividends["ex_dividend_date"]),
            "record_date": pd.to_datetime(dividends["record_date"]),
            "declared_date": pd.to_datetime(dividends["declaration_date"]),
            "pay_date": pd.to_datetime(dividends["pay_date"]),
        }
    )
    dividends = _with_sids(dividends, tickers, sids, "ex_date")
    return dividends[
        ["sid", "amount", "ex_date", "record_date", "declared_date", "pay_date"]
    ]


bundles.register("massive", massive_equities(), calendar_name=CALENDAR_NAME)
