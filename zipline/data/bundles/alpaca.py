"""US equities from Alpaca's market data API.

The ``alpaca`` bundle downloads, with the user's API keys:

- unadjusted daily bars from the consolidated (SIP) feed since 2016, each
  labelled with the ticker it traded under that day, cached so that later
  ingestions only download new sessions;
- the assets listed today, and corporate actions: renames, the mergers and
  removals that end a security, splits and cash dividends.

Each bar's ticker is followed through later renames to the event that ended
the security, or else to the asset listed under the ticker today, which tells
which security the bar is of. So a renamed company (e.g. FB to META) keeps one
sid, and a ticker reused by another company gets a new one. Securities are
keyed by the first ticker and session of their bars, which later renames and
delistings don't change, so a security keeps its sid in every ingestion.

Alpaca's free plan allows 200 requests a minute and historical SIP data since
2016. Its terms don't allow redistributing the data, so the downloaded data
stays on the user's machine.
"""

import json
import logging
import os
import re

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

DATA_URL = "https://data.alpaca.markets"
TRADING_URL = "https://paper-api.alpaca.markets"

#: Alpaca's exchange names, and their MICs. Assets only traded over the
#: counter aren't ingested.
EXCHANGES = {
    "NYSE": "XNYS",
    "NASDAQ": "XNAS",
    "ARCA": "ARCX",
    "AMEX": "XASE",
    "BATS": "BATS",
}

#: The free plan's rate limit, and the first session with SIP data.
FREE_PLAN_CALLS_PER_MINUTE = 200
FIRST_SESSION = pd.Timestamp("2016-01-04")

#: How long after the month they were processed in corporate actions are
#: downloaded again, since Alpaca reports some late.
DEFAULT_REFRESH = pd.Timedelta(days=90)

#: How long after a session's close its daily bars are taken to be final.
SETTLE_DELAY = pd.Timedelta(hours=4)

CALENDAR_NAME = "XNYS"

#: How many tickers to ask for bars of in one request.
SYMBOLS_PER_REQUEST = 200

PRICE_COLUMNS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}

#: Corporate actions that end a security, with the columns holding its ticker
#: and the date it ended.
ENDINGS = {
    "cash_mergers": ("acquiree_symbol", "effective_date"),
    "stock_mergers": ("acquiree_symbol", "effective_date"),
    "stock_and_cash_mergers": ("acquiree_symbol", "effective_date"),
    "worthless_removals": ("symbol", "process_date"),
}
ACTION_TYPES = [
    "name_change",
    "cash_merger",
    "stock_merger",
    "stock_and_cash_merger",
    "worthless_removal",
    "forward_split",
    "reverse_split",
    "cash_dividend",
]

# The end of time, for coverage that never runs out.
_FOREVER = pd.Timestamp.max.floor("D")

# A security can be renamed at most this many times.
_MAX_RENAMES = 20

_INVALID_SYMBOL = re.compile(r"invalid symbol: (\S+)")


def alpaca_equities(
    key_id=None,
    secret_key=None,
    start_session=None,
    calls_per_minute=None,
    refresh=DEFAULT_REFRESH,
):
    """An ingest function for US equities from Alpaca.

    Arguments left as None are read from the environment given to
    ``ingest``, and otherwise default to what Alpaca's free plan allows.

    Parameters
    ----------
    key_id, secret_key : str, optional
        The Alpaca API key. Default to ``APCA_API_KEY_ID`` and
        ``APCA_API_SECRET_KEY``, the variables Alpaca's own tools use.
    start_session : pd.Timestamp, optional
        The first session to ingest. Defaults to ``ALPACA_START_DATE``, or
        else 2016-01-04, when Alpaca's data starts.
    calls_per_minute : float, optional
        The plan's rate limit. Defaults to ``ALPACA_CALLS_PER_MINUTE``, or
        else 200, the free plan's; 0 means unlimited.
    refresh : pd.Timedelta, optional
        Corporate actions are cached by the month they were processed in, but
        Alpaca reports some late, so the months within ``refresh`` of today
        are downloaded again at each ingestion.

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
        key = key_id or environ.get("APCA_API_KEY_ID")
        secret = secret_key or environ.get("APCA_API_SECRET_KEY")
        if not (key and secret):
            raise ValueError(
                "Please set your APCA_API_KEY_ID and APCA_API_SECRET_KEY"
                " environment variables and retry. Get free API keys for a"
                " paper trading account at https://alpaca.markets."
            )
        now = pd.Timestamp.now(tz="UTC")
        start = start_session
        if start is None and environ.get("ALPACA_START_DATE"):
            start = pd.Timestamp(environ["ALPACA_START_DATE"])
        if start is None:
            start = FIRST_SESSION
        start = max(start, first_session)
        rate = calls_per_minute
        if rate is None:
            rate = float(
                environ.get("ALPACA_CALLS_PER_MINUTE", FREE_PLAN_CALLS_PER_MINUTE)
            )

        session = RateLimitedSession(
            calls_per_minute=rate or None,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        try:
            _ingest(
                session,
                pth.cache_path(["alpaca"], environ=environ),
                asset_db_writer,
                daily_bar_writer,
                adjustment_writer,
                completed_sessions(calendar, start, last_session, now, SETTLE_DELAY),
                now.tz_localize(None).normalize(),
                refresh,
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
    sessions,
    today,
    refresh,
    show_progress,
):
    if not len(sessions):
        raise ValueError("There are no completed sessions to ingest.")
    listed = _listed_assets(session)
    actions = _corporate_actions(
        session,
        DailyCache(os.path.join(cache_dir, "actions")),
        sessions[0],
        today,
        refresh,
        show_progress,
    )
    events = _events(actions)

    bar_cache = DailyCache(os.path.join(cache_dir, "daily"))
    _download_bars(
        session,
        bar_cache,
        os.path.join(cache_dir, "coverage.parquet"),
        sessions,
        _needs(sessions, listed.index, events),
        show_progress,
    )
    sessions = sessions.difference(bar_cache.missing(sessions))
    if not len(sessions):
        raise ValueError("Alpaca has no bars for any session to ingest yet.")

    bars = bar_cache.get(sessions)
    bars["security"] = _resolve(events, listed.index, bars["symbol"], bars["date"])
    unknown = bars["security"].isna()
    if unknown.any():
        log.info(
            "Skipping %d tickers that are neither listed today nor ended by a"
            " corporate action.",
            bars.loc[unknown, "symbol"].nunique(),
        )
        bars = bars[~unknown]

    # A security is keyed by the first ticker and session of its bars.
    first = bars.sort_values("date").drop_duplicates("security")
    keys = pd.Series(
        (first["symbol"] + ":" + first["date"].dt.strftime("%Y-%m-%d")).to_numpy(),
        index=first["security"].to_numpy(),
    )
    sids = SidMap(os.path.join(cache_dir, "sids.parquet")).assign(
        pd.Series(keys.to_numpy(), index=keys.to_numpy())
    )
    security_sids = keys.map(sids)
    bars["sid"] = security_sids.loc[bars["security"]].to_numpy()
    # If a security traded under two tickers on one session, e.g. on the day
    # of a rename, keep the busier one.
    bars = bars.sort_values("volume").drop_duplicates(["sid", "date"], keep="last")

    info = pd.DataFrame(
        {"asset_name": None, "exchange": "UNKNOWN"}, index=security_sids.index
    )
    is_listed = info.index.str.startswith("listed:")
    symbols = info.index[is_listed].str.removeprefix("listed:")
    info.loc[is_listed, "asset_name"] = listed.loc[symbols, "name"].to_numpy()
    info.loc[is_listed, "exchange"] = listed.loc[symbols, "exchange"].to_numpy()
    info.index = security_sids.loc[info.index].to_numpy()
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
        assets=set(security_sids),
        show_progress=show_progress,
    )

    # Splits and dividends on the first session only affect earlier prices
    # and holdings, which the bundle doesn't have, so they are left out.
    in_range = {"after": sessions[0], "until": sessions[-1]}
    adjustment_writer.write(
        splits=_splits(actions, events, listed, security_sids, **in_range),
        dividends=_dividends(actions, events, listed, security_sids, **in_range),
    )


def _listed_assets(session):
    """The US equities listed on an exchange today.

    Returns
    -------
    listed : pd.DataFrame
        Indexed by ticker, with columns ``name`` and ``exchange`` (its MIC).
    """
    assets = pd.DataFrame(
        session.get_json(
            f"{TRADING_URL}/v2/assets",
            {"status": "active", "asset_class": "us_equity"},
        )
    ).reindex(columns=["symbol", "name", "exchange"])
    assets = assets[assets["exchange"].isin(EXCHANGES)]
    return pd.DataFrame(
        {
            "name": assets["name"].replace("", None).to_numpy(),
            "exchange": assets["exchange"].map(EXCHANGES).to_numpy(),
        },
        index=pd.Index(assets["symbol"].to_numpy(), name="symbol"),
    )


def _corporate_actions(session, month_cache, start, today, refresh, show_progress):
    """The corporate actions of ``ACTION_TYPES`` processed from ``start``'s
    month to ``today``, as a DataFrame per type.

    Months that ended more than ``refresh`` before today are cached in
    ``month_cache``; later ones are downloaded at each ingestion.
    """
    months = pd.date_range(start.replace(day=1), today, freq="MS").as_unit("ns")
    settled = months[months + pd.offsets.MonthEnd(0) < today - refresh]
    recent = months.difference(settled)
    to_download = settled[settled.isin(month_cache.missing(settled))].union(recent)
    downloaded = {}
    with maybe_show_progress(
        to_download,
        show_progress,
        label="Downloading Alpaca corporate actions:",
    ) as it:
        for month in it:
            end = min(month + pd.offsets.MonthEnd(0), today)
            downloaded[month] = _month_actions(session, month, end)
            if month in settled:
                month_cache.put(month, downloaded[month])
    cached = settled.difference(to_download)
    frames = [month_cache.get(cached).drop(columns="date"), *downloaded.values()]
    records = pd.concat(frames, ignore_index=True)
    return {
        kind: pd.DataFrame([json.loads(record) for record in group["record"]])
        for kind, group in records.groupby("kind")
    }


def _month_actions(session, start, end):
    """The corporate actions processed from ``start`` to ``end``.

    Returns
    -------
    actions : pd.DataFrame
        Columns ``kind``, e.g. ``"cash_dividends"``, and ``record``, the
        action as JSON, since each kind has its own fields.
    """
    params = {
        "types": ",".join(ACTION_TYPES),
        "start": f"{start:%Y-%m-%d}",
        "end": f"{end:%Y-%m-%d}",
        "limit": 1000,
    }
    kinds, records = [], []
    while True:
        data = session.get_json(f"{DATA_URL}/v1/corporate-actions", params)
        for kind, actions in (data.get("corporate_actions") or {}).items():
            kinds.extend([kind] * len(actions))
            records.extend(json.dumps(action) for action in actions)
        if not data.get("next_page_token"):
            break
        params = params | {"page_token": data["next_page_token"]}
    return pd.DataFrame(
        {
            "kind": pd.Series(kinds, dtype=object),
            "record": pd.Series(records, dtype=object),
        }
    )


def _dates(frame, column):
    if column not in frame:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    return parse_dates(frame[column])


def _events(actions):
    """Renames, and the endings of securities.

    Returns
    -------
    events : pd.DataFrame
        Columns ``symbol``, ``date`` (the first session it no longer traded
        under ``symbol``), ``new_symbol`` (for renames) and ``end`` (for
        endings, a name for the security that ended).
    """
    frames = []
    renames = actions.get("name_changes")
    if renames is not None and len(renames):
        renames = renames[renames["old_symbol"] != renames["new_symbol"]]
        frames.append(
            pd.DataFrame(
                {
                    "symbol": renames["old_symbol"],
                    "date": _dates(renames, "process_date"),
                    "new_symbol": renames["new_symbol"],
                    "end": None,
                }
            )
        )
    for kind, (symbol_column, date_column) in ENDINGS.items():
        endings = actions.get(kind)
        if endings is None or not len(endings):
            continue
        if kind == "stock_mergers":
            # A one-for-one merger into a security with the same ticker, or
            # into one without a ticker of its own that takes it over, e.g. an
            # ETF reorganization or a new holding company (ADTRAN, Jacobs in
            # 2022), continues the security.
            acquirer = endings["acquirer_symbol"].fillna("")
            one_for_one = endings["acquiree_rate"] == endings["acquirer_rate"]
            continues = one_for_one & (
                (acquirer == "") | (acquirer == endings[symbol_column])
            )
            endings = endings[~continues]
        dates = _dates(endings, date_column)
        frames.append(
            pd.DataFrame(
                {
                    "symbol": endings[symbol_column],
                    "date": dates,
                    "new_symbol": None,
                    "end": "ended:"
                    + endings[symbol_column]
                    + ":"
                    + dates.dt.strftime("%Y-%m-%d"),
                }
            )
        )
    if not frames:
        return pd.DataFrame(
            {
                "symbol": pd.Series(dtype=object),
                "date": pd.Series(dtype="datetime64[ns]"),
                "new_symbol": pd.Series(dtype=object),
                "end": pd.Series(dtype=object),
            }
        )
    return (
        pd.concat(frames, ignore_index=True)
        .dropna(subset=["symbol", "date"])
        .sort_values("date", kind="stable")
    )


def _resolve(events, listed, symbols, dates):
    """The security each of ``symbols`` meant on the matching date.

    A ticker is followed through its renames after the date, to the event
    that ended the security, or else to the asset listed under it today.

    Returns
    -------
    securities : np.ndarray[object]
        ``"ended:<ticker>:<date>"`` or ``"listed:<ticker>"``, or None if the
        ticker is neither listed today nor ended.
    """
    result = pd.Series(None, index=range(len(symbols)), dtype=object)
    todo = pd.DataFrame(
        {
            "symbol": pd.Series(symbols).to_numpy(),
            "date": pd.DatetimeIndex(dates).as_unit("ns"),
            "position": range(len(symbols)),
        }
    )
    events = events.rename(columns={"date": "event_date"})
    for _ in range(_MAX_RENAMES):
        if todo.empty:
            break
        matched = pd.merge_asof(
            todo.sort_values("date"),
            events,
            left_on="date",
            right_on="event_date",
            by="symbol",
            direction="forward",
            allow_exact_matches=False,
        )
        current = matched["event_date"].isna()
        is_listed = current & matched["symbol"].isin(listed)
        result[matched.loc[is_listed, "position"]] = (
            "listed:" + matched.loc[is_listed, "symbol"]
        ).to_numpy()
        ended = matched["end"].notna()
        result[matched.loc[ended, "position"]] = matched.loc[ended, "end"].to_numpy()
        renamed = ~current & ~ended
        todo = pd.DataFrame(
            {
                "symbol": matched.loc[renamed, "new_symbol"].to_numpy(),
                "date": matched.loc[renamed, "event_date"].to_numpy(),
                "position": matched.loc[renamed, "position"].to_numpy(),
            }
        )
    return result.to_numpy()


def _needs(sessions, listed, events):
    """The last session of ``sessions`` each ticker's bars are needed
    through: the last for tickers listed today, and for tickers with later
    events, the last of the year of their last event.

    Returns
    -------
    needs : pd.Series
        The session, indexed by ticker.
    """
    last_of_year = pd.Series(sessions, index=sessions).groupby(sessions.year).max()
    changed = pd.concat(
        [
            events[["symbol", "date"]],
            events[["new_symbol", "date"]].rename(columns={"new_symbol": "symbol"}),
        ]
    ).dropna()
    changed = changed[changed["date"] > sessions[0]]
    last_event = changed.groupby("symbol")["date"].max()
    needs = last_event.dt.year.map(last_of_year).fillna(sessions[-1])
    needs = pd.concat(
        [needs, pd.Series(sessions[-1], index=pd.Index(listed, dtype=object))]
    )
    return needs.groupby(level=0).max().astype("datetime64[ns]")


def _download_bars(session, bar_cache, coverage_path, sessions, needs, show_progress):
    """Download the bars ``needs`` asks for into ``bar_cache``.

    The cache has a file of bars per session, for the tickers asked for so
    far. Which sessions each ticker was asked for is kept in a coverage file,
    so that tickers that become needed later, e.g. when Alpaca reports a
    merger late, are downloaded for the sessions they are missing.
    """
    coverage = _read_coverage(coverage_path)
    pieces = _missing(coverage, sessions, needs)
    if pieces.empty:
        return
    log.info("Downloading daily bars for %d tickers.", pieces["symbol"].nunique())
    # Download a year at a time, to bound the memory needed.
    batches = []
    for (start, end), group in pieces.groupby(["start", "end"]):
        symbols = sorted(group["symbol"])
        in_piece = sessions[(sessions >= start) & (sessions <= end)]
        for _, year in pd.Series(in_piece, index=in_piece).groupby(in_piece.year):
            for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
                batches.append(
                    (symbols[i : i + SYMBOLS_PER_REQUEST], year.iloc[0], year.iloc[-1])
                )
    with maybe_show_progress(
        batches, show_progress, label="Downloading Alpaca daily bars:"
    ) as it:
        unknown = set()
        for symbols, start, end in it:
            bars, unknown_symbols = _bars(session, symbols, start, end)
            _add_bars(bar_cache, bars)
            unknown.update(unknown_symbols)

    # Tickers are covered through the last session with any bars; later ones
    # aren't published yet.
    published = sessions.difference(bar_cache.missing(sessions))
    if len(published) < len(sessions):
        log.warning(
            "Alpaca has no bars for %s yet.",
            ", ".join(str(day.date()) for day in sessions.difference(published)[:5]),
        )
    if not len(published):
        return
    last = published[-1]
    pieces = pieces[pieces["start"] <= last].assign(end=pieces["end"].clip(upper=last))
    # Tickers Alpaca doesn't know won't have bars later either.
    unknown = pd.DataFrame(
        {"symbol": sorted(unknown), "start": sessions[0], "end": _FOREVER}
    )
    covered = pd.concat(
        [coverage.reset_index(), pieces[["symbol", "start", "end"]], unknown],
        ignore_index=True,
    )
    coverage = covered.groupby("symbol").agg(start=("start", "min"), end=("end", "max"))
    _write_coverage(coverage_path, coverage)


def _read_coverage(path):
    if os.path.exists(path):
        return pd.read_parquet(path)
    return pd.DataFrame(
        {
            "start": pd.Series(dtype="datetime64[ns]"),
            "end": pd.Series(dtype="datetime64[ns]"),
        },
        index=pd.Index([], dtype=object, name="symbol"),
    )


def _write_coverage(path, coverage):
    tmp = path + ".tmp"
    coverage.to_parquet(tmp)
    os.replace(tmp, path)


def _missing(coverage, sessions, needs):
    """The ranges of sessions each ticker in ``needs`` hasn't been downloaded
    for, from the first of ``sessions`` through the ticker's needed session.

    Returns
    -------
    pieces : pd.DataFrame
        Columns ``symbol``, ``start`` and ``end``, a row per missing range.
    """
    first = sessions[0]
    frame = pd.DataFrame({"need": needs}).join(coverage)
    rows = []
    for symbol, need, start, end in frame.itertuples():
        if pd.isna(start):
            rows.append((symbol, first, need))
            continue
        if first < start:
            before = sessions[sessions < start]
            if len(before):
                rows.append((symbol, first, before[-1]))
        if need > end:
            after = sessions[(sessions > end) & (sessions <= need)]
            if len(after):
                rows.append((symbol, after[0], need))
    return pd.DataFrame(rows, columns=["symbol", "start", "end"])


def _add_bars(bar_cache, bars):
    """Add ``bars`` to the cached bars of their sessions."""
    for day, day_bars in bars.groupby("date"):
        day_bars = day_bars.drop(columns="date")
        if not len(bar_cache.missing(pd.DatetimeIndex([day]))):
            cached = bar_cache.get([day]).drop(columns="date")
            day_bars = pd.concat([cached, day_bars]).drop_duplicates(
                "symbol", keep="last"
            )
        bar_cache.put(day, day_bars)


def _bars(session, symbols, start, end):
    """The bars of ``symbols`` from ``start`` to ``end``, labelled with the
    ticker each traded under.

    Returns
    -------
    bars : pd.DataFrame
    unknown : list[str]
        The tickers Alpaca doesn't know, which are left out.
    """
    symbols = list(symbols)
    unknown = []
    while symbols:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": f"{start:%Y-%m-%d}",
            "end": f"{end:%Y-%m-%d}",
            "adjustment": "raw",
            "feed": "sip",
            # Label bars with the ticker they traded under, rather than
            # mapping tickers to today's names.
            "asof": "-",
            "limit": 10000,
        }
        try:
            records = []
            while True:
                data = session.get_json(f"{DATA_URL}/v2/stocks/bars", params)
                for symbol, bars in (data.get("bars") or {}).items():
                    records.extend({"symbol": symbol, **bar} for bar in bars)
                if not data.get("next_page_token"):
                    break
                params = params | {"page_token": data["next_page_token"]}
        except requests.HTTPError as exc:
            invalid = _invalid_symbol(exc)
            if invalid is None or invalid not in symbols:
                raise
            log.info("Alpaca doesn't know the ticker %s; skipping it.", invalid)
            symbols.remove(invalid)
            unknown.append(invalid)
            continue
        frame = pd.DataFrame(records, columns=["symbol", "t", *PRICE_COLUMNS]).rename(
            columns=PRICE_COLUMNS
        )
        # Alpaca repeats the last price with no volume for tickers that
        # stopped trading, e.g. under a ticker renamed months before.
        frame = frame[frame["volume"] > 0]
        # Daily bars are timestamped at midnight in New York.
        frame["date"] = (
            pd.to_datetime(frame.pop("t"), utc=True)
            .dt.tz_convert("America/New_York")
            .dt.tz_localize(None)
            .dt.normalize()
            .astype("datetime64[ns]")
        )
        return frame, unknown
    empty = pd.DataFrame(
        {
            "symbol": pd.Series(dtype=object),
            **{column: pd.Series(dtype="float64") for column in PRICE_COLUMNS.values()},
            "date": pd.Series(dtype="datetime64[ns]"),
        }
    )
    return empty, unknown


def _invalid_symbol(exc):
    """The ticker a request failed on for being unknown, if it did."""
    response = exc.response
    if response is None or response.status_code != 400:
        return None
    try:
        message = response.json().get("message", "")
    except ValueError:
        return None
    match = _INVALID_SYMBOL.fullmatch(message)
    return match.group(1) if match else None


def _with_sids(frame, events, listed, sids, date_column):
    """``frame`` with a ``sid`` column, keeping the rows for securities with
    bars.
    """
    securities = pd.Series(
        _resolve(events, listed.index, frame["symbol"], frame[date_column]),
        index=frame.index,
    )
    frame = frame.assign(sid=securities.map(sids))
    return frame[frame["sid"].notna()].astype({"sid": "int64"})


def _in_range(frame, column, after, until):
    return frame[(frame[column] > after) & (frame[column] <= until)]


def _splits(actions, events, listed, sids, after, until):
    frames = [
        actions[kind]
        for kind in ("forward_splits", "reverse_splits")
        if kind in actions and len(actions[kind])
    ]
    if not frames:
        return None
    splits = pd.concat(frames, ignore_index=True)
    splits = pd.DataFrame(
        {
            "symbol": splits["symbol"],
            "effective_date": _dates(splits, "ex_date"),
            # The price multiplier for bars before the split; 0.5 for a
            # 2-for-1 split.
            "ratio": splits["old_rate"].astype("float64")
            / splits["new_rate"].astype("float64"),
        }
    )
    splits = _in_range(splits, "effective_date", after, until)
    splits = _with_sids(splits, events, listed, sids, "effective_date")
    return splits[["sid", "ratio", "effective_date"]]


def _dividends(actions, events, listed, sids, after, until):
    dividends = actions.get("cash_dividends")
    if dividends is None or not len(dividends):
        return None
    dividends = pd.DataFrame(
        {
            "symbol": dividends["symbol"],
            "amount": dividends["rate"].astype("float64"),
            "ex_date": _dates(dividends, "ex_date"),
            "record_date": _dates(dividends, "record_date"),
            "declared_date": pd.NaT,
            "pay_date": _dates(dividends, "payable_date"),
        }
    )
    dividends = _in_range(dividends, "ex_date", after, until)
    dividends = _with_sids(dividends, events, listed, sids, "ex_date")
    return dividends[
        ["sid", "amount", "ex_date", "record_date", "declared_date", "pay_date"]
    ]


bundles.register("alpaca", alpaca_equities(), calendar_name=CALENDAR_NAME)
