"""Building blocks for bundles that download from a market data vendor's API.

A vendor bundle downloads the vendor's raw (unadjusted) daily bars, its
reference data (which security a ticker meant when) and its splits and
dividends, and writes them with the writers ``ingest`` passes it. This module
has the parts that don't depend on the vendor:

- :class:`RateLimitedSession` sends HTTP requests no faster than the vendor's
  rate limit allows, and retries rate-limited and failed requests.
- :class:`DailyBarCache` keeps each session's bars for the whole market under
  ``$ZIPLINE_ROOT/cache/<vendor>``, so each ingestion only downloads the
  sessions earlier ones didn't.
- :class:`SidMap` gives each security the same sid in every ingestion.
- :func:`equities_frame` and :func:`exchanges_frame` build the asset metadata
  from bars labelled with a security and a ticker, with ticker changes as
  symbol mappings.
- :func:`completed_sessions` lists the sessions whose bars are final.
"""

import logging
import os
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from zipline.utils.calendar_utils import has_calendar

log = logging.getLogger(__name__)


class RateLimitedSession:
    """Send HTTP GET requests at most ``calls_per_minute`` times a minute.

    Requests that are rate-limited (HTTP 429), fail on the server (5xx) or
    lose their connection are retried after a growing delay, or the delay the
    server asks for in ``Retry-After``.

    Parameters
    ----------
    calls_per_minute : float, optional
        The vendor's rate limit. None means unlimited.
    headers : dict, optional
        Headers to send with every request, e.g. for authentication.
    retries : int, optional
        How many times to retry a request before giving up.
    backoff : float, optional
        The delay in seconds before the first retry; it doubles each time.
    timeout : float, optional
        The timeout in seconds for each request.
    sleep, clock : callable, optional
        ``time.sleep`` and ``time.monotonic``, replaceable for testing.
    """

    def __init__(
        self,
        calls_per_minute=None,
        headers=None,
        retries=5,
        backoff=2.0,
        timeout=60.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self._interval = 60.0 / calls_per_minute if calls_per_minute else 0.0
        self._retries = retries
        self._backoff = backoff
        self._timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._last_call = None
        self._session = requests.Session()
        self._session.headers.update(headers or {})

    def close(self):
        self._session.close()

    def _wait_for_slot(self):
        if self._last_call is not None:
            remaining = self._last_call + self._interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_call = self._clock()

    def get_json(self, url, params=None):
        """GET ``url`` and return its JSON body.

        Raises
        ------
        requests.HTTPError
            If the request fails with a client error other than 429, or still
            fails after all retries.
        """
        delay = self._backoff
        for attempt in range(self._retries + 1):
            self._wait_for_slot()
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self._retries:
                    raise
                log.warning("Request to %s failed (%s); retrying.", url, exc)
                wait = delay
            else:
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self._retries:
                    response.raise_for_status()
                    return response.json()
                log.warning(
                    "Request to %s returned HTTP %s; retrying.",
                    url,
                    response.status_code,
                )
                wait = _retry_after(response, delay)
            self._sleep(wait)
            delay *= 2
        raise AssertionError("unreachable")


def _retry_after(response, default):
    try:
        return max(float(response.headers["Retry-After"]), 0.0)
    except (KeyError, ValueError):
        return default


class DailyBarCache:
    """Downloaded daily bars for the whole market, one Parquet file per
    session.

    Parameters
    ----------
    path : str
        The directory to keep the files in.
    """

    def __init__(self, path):
        self._path = path

    def _file(self, session):
        return os.path.join(self._path, f"{session:%Y-%m-%d}.parquet")

    def missing(self, sessions):
        """The sessions in ``sessions`` that aren't cached."""
        return sessions[[not os.path.exists(self._file(s)) for s in sessions]]

    def put(self, session, bars):
        """Cache ``session``'s bars, a DataFrame with any columns."""
        os.makedirs(self._path, exist_ok=True)
        path = self._file(session)
        tmp = path + ".tmp"
        pq.write_table(
            pa.Table.from_pandas(bars, preserve_index=False),
            tmp,
            compression="zstd",
        )
        os.replace(tmp, path)

    def get(self, sessions):
        """The cached bars for ``sessions``, with a ``date`` column holding
        each bar's session.

        Raises
        ------
        KeyError
            If a session isn't cached.
        """
        frames = []
        for session in sessions:
            path = self._file(session)
            if not os.path.exists(path):
                raise KeyError(session)
            frame = pd.read_parquet(path)
            frame.insert(0, "date", session)
            frames.append(frame)
        if not frames:
            return pd.DataFrame({"date": pd.DatetimeIndex([], dtype="datetime64[ns]")})
        return pd.concat(frames, ignore_index=True)


class SidMap:
    """Sids for security keys, kept in a file so that a security has the same
    sid in every ingestion.

    Parameters
    ----------
    path : str
        The Parquet file to keep the map in.
    """

    def __init__(self, path):
        self._path = path

    def assign(self, keys):
        """The sids for ``keys``, giving new keys the next unused sids in
        sorted order.

        Returns
        -------
        sids : pd.Series
            The sid for each key, indexed by key.
        """
        if os.path.exists(self._path):
            known = pd.read_parquet(self._path)["sid"]
        else:
            known = pd.Series([], index=pd.Index([], dtype=object), dtype="int64")
        new = sorted(set(keys).difference(known.index))
        if new:
            start = int(known.max()) + 1 if len(known) else 0
            known = pd.concat(
                [
                    known,
                    pd.Series(
                        range(start, start + len(new)),
                        index=pd.Index(new, dtype=object),
                        dtype="int64",
                    ),
                ]
            )
            known.index.name = "key"
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            known.rename("sid").to_frame().to_parquet(tmp)
            os.replace(tmp, self._path)
        return known.loc[list(keys)]


def equities_frame(bars, info):
    """Asset metadata for securities with bars.

    Parameters
    ----------
    bars : pd.DataFrame
        Columns ``sid``, ``symbol`` and ``date``: the ticker each security
        traded under on each session it has a bar.
    info : pd.DataFrame
        Indexed by sid, with columns ``asset_name`` and ``exchange``.

    Returns
    -------
    equities : pd.DataFrame
        The ``equities`` argument of ``AssetDBWriter.write``, indexed by sid,
        with one row per run of sessions a security traded under one ticker,
        so that ticker changes become symbol mappings.
    """
    bars = bars[["sid", "symbol", "date"]].sort_values(["sid", "date"])
    # A new run starts at each security's first bar and at each ticker change.
    new_run = (bars["sid"] != bars["sid"].shift()) | (
        bars["symbol"] != bars["symbol"].shift()
    )
    runs = (
        bars.assign(run=new_run.cumsum())
        .groupby("run")
        .agg(
            sid=("sid", "first"),
            symbol=("symbol", "first"),
            start_date=("date", "min"),
            end_date=("date", "max"),
        )
        .set_index("sid")
    )
    equities = runs.join(info[["asset_name", "exchange"]])
    last_bar = runs.groupby(level=0)["end_date"].max()
    # Positions are closed the day after an asset's last bar.
    equities["auto_close_date"] = last_bar.reindex(equities.index) + pd.Timedelta(
        days=1
    )
    return equities


def exchanges_frame(exchanges, calendar_name, country_code):
    """The ``exchanges`` argument of ``AssetDBWriter.write``.

    Parameters
    ----------
    exchanges : iterable[str]
        The exchanges' names, e.g. their MICs.
    calendar_name : str
        The calendar of exchanges that don't have a calendar under their own
        name.
    country_code : str
        The exchanges' country.
    """
    exchanges = sorted(set(exchanges))
    return pd.DataFrame(
        {
            "exchange": exchanges,
            # An asset's exchange is the canonical name, which also names
            # its calendar.
            "canonical_name": [
                name if has_calendar(name) else calendar_name for name in exchanges
            ],
            "country_code": country_code,
        }
    )


def completed_sessions(calendar, start_session, end_session, now, delay):
    """The sessions from ``start_session`` to ``end_session`` whose bars are
    final: those that closed at least ``delay`` before ``now``.
    """
    sessions = calendar.sessions_in_range(start_session, end_session)
    closes = calendar.closes.reindex(sessions)
    return sessions[(closes + delay <= now).to_numpy()]
