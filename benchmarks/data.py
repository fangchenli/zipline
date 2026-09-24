"""Synthetic market data for the benchmarks.

Everything the benchmarks need goes through this module: generating
deterministic prices, writing them in a storage format ("backend"), and
opening readers over the result. Supporting a new storage format means adding
a backend here; the benchmarks are parameterized over ``DAILY_BACKENDS`` and
``MINUTE_BACKENDS``.
"""

import os
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from zipline.assets import AssetDBWriter, AssetFinder
from zipline.data.adjustments import SQLiteAdjustmentReader, SQLiteAdjustmentWriter
from zipline.data.bcolz_daily_bars import BcolzDailyBarReader, BcolzDailyBarWriter
from zipline.data.data_portal import DataPortal
from zipline.data.minute_bars import (
    US_EQUITIES_MINUTES_PER_DAY,
    BcolzMinuteBarReader,
    BcolzMinuteBarWriter,
)
from zipline.data.parquet_daily_bars import (
    ParquetDailyBarReader,
    ParquetDailyBarWriter,
)
from zipline.data.parquet_minute_bars import (
    ParquetMinuteBarReader,
    ParquetMinuteBarWriter,
)
from zipline.utils.calendar_utils import get_calendar

DAILY_BACKENDS = ["bcolz", "parquet"]
MINUTE_BACKENDS = ["bcolz", "parquet"]
BACKENDS = sorted(set(DAILY_BACKENDS) | set(MINUTE_BACKENDS))

CALENDAR = "XNYS"
SEED = 1234

# Sizes are chosen so the whole suite builds in about a minute while still
# being large enough for storage costs to dominate over Python overhead.
DAILY_START = pd.Timestamp("2014-01-02")
DAILY_END = pd.Timestamp("2023-12-29")  # 10 years
DAILY_SIDS = 500

MINUTE_START = pd.Timestamp("2023-01-03")
MINUTE_END = pd.Timestamp("2023-03-31")  # one quarter
MINUTE_SIDS = 50


def _random_walk_ohlcv(rng, n, start_price, volatility):
    """OHLCV columns for ``n`` bars following a geometric random walk."""
    returns = rng.normal(0.0, volatility, n)
    close = start_price * np.exp(np.cumsum(returns))
    spread = np.abs(rng.normal(0.0, 0.005, n)) * close
    open_ = close * (1 + rng.normal(0.0, 0.002, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.integers(1_000, 1_000_000, n).astype(np.float64)
    return {
        "open": open_.round(2),
        "high": high.round(2),
        "low": low.round(2),
        "close": close.round(2),
        "volume": volume,
    }


def daily_frames(calendar, sids, start, end, seed=SEED):
    """Yield (sid, DataFrame) pairs of daily bars indexed by session."""
    sessions = calendar.sessions_in_range(start, end)
    rng = np.random.default_rng(seed)
    for sid in sids:
        cols = _random_walk_ohlcv(rng, len(sessions), rng.uniform(10, 500), 0.01)
        yield sid, pd.DataFrame(cols, index=sessions)


def minute_frames(calendar, sids, start, end, seed=SEED):
    """Yield (sid, DataFrame) pairs of minute bars indexed by UTC minute."""
    minutes = calendar.sessions_minutes(start, end)
    rng = np.random.default_rng(seed)
    for sid in sids:
        # Minute moves are much smaller than daily ones.
        cols = _random_walk_ohlcv(rng, len(minutes), rng.uniform(10, 500), 0.0005)
        yield sid, pd.DataFrame(cols, index=minutes)


def equity_info(sids, start, end):
    return pd.DataFrame(
        {
            "symbol": [f"S{sid:04d}" for sid in sids],
            "start_date": start,
            "end_date": end,
            "exchange": "NYSE",
        },
        index=list(sids),
    )


def splits(sids, sessions, seed=SEED):
    """A 2:1 split for every tenth asset, at a random session."""
    rng = np.random.default_rng(seed)
    split_sids = list(sids)[::10]
    dates = rng.choice(sessions[1:], len(split_sids))
    return pd.DataFrame(
        {
            "sid": split_sids,
            "effective_date": pd.DatetimeIndex(dates)
            .as_unit("s")
            .to_numpy()
            .view("i8"),
            "ratio": 0.5,
        }
    )


@dataclass(frozen=True)
class Bundle:
    """Paths to a synthetic bundle written in one storage format."""

    root: str
    backend: str

    @property
    def assets_path(self):
        return os.path.join(self.root, "assets.sqlite")

    @property
    def adjustments_path(self):
        return os.path.join(self.root, "adjustments.sqlite")

    @property
    def daily_path(self):
        return os.path.join(self.root, "daily_equities")

    @property
    def minute_path(self):
        return os.path.join(self.root, "minute_equities")

    @cached_property
    def calendar(self):
        return get_calendar(CALENDAR)

    def minutes(self):
        """The minutes covered by the minute bars."""
        return self.calendar.sessions_minutes(MINUTE_START, MINUTE_END)

    def minute_sessions(self):
        """The sessions covered by the minute bars."""
        return self.calendar.sessions_in_range(MINUTE_START, MINUTE_END)

    def asset_finder(self):
        return AssetFinder(self.assets_path)

    def daily_reader(self):
        if self.backend == "bcolz":
            return BcolzDailyBarReader(self.daily_path)
        if self.backend == "parquet":
            return ParquetDailyBarReader(self.daily_path)
        raise ValueError(self.backend)

    def minute_reader(self):
        if self.backend == "bcolz":
            return BcolzMinuteBarReader(self.minute_path)
        if self.backend == "parquet":
            return ParquetMinuteBarReader(self.minute_path)
        raise ValueError(self.backend)

    def adjustment_reader(self):
        return SQLiteAdjustmentReader(self.adjustments_path)

    def data_portal(self, minute=False):
        daily = self.daily_reader()
        return DataPortal(
            self.asset_finder(),
            self.calendar,
            first_trading_day=daily.first_trading_day,
            equity_daily_reader=daily,
            equity_minute_reader=self.minute_reader() if minute else None,
            adjustment_reader=self.adjustment_reader(),
        )


def write_daily(backend, path, calendar, frames, start, end):
    if backend == "bcolz":
        BcolzDailyBarWriter(path, calendar, start, end).write(frames)
    elif backend == "parquet":
        ParquetDailyBarWriter(path, calendar, start, end).write(frames)
    else:
        raise ValueError(backend)


def write_minute(backend, path, calendar, frames, start, end):
    if backend == "bcolz":
        os.makedirs(path, exist_ok=True)
        BcolzMinuteBarWriter(
            path,
            calendar,
            start,
            end,
            minutes_per_day=US_EQUITIES_MINUTES_PER_DAY,
        ).write(frames)
    elif backend == "parquet":
        ParquetMinuteBarWriter(path, calendar, start, end).write(frames)
    else:
        raise ValueError(backend)


def build_bundle(root, backend):
    """Write the full synthetic bundle into ``root`` and return it."""
    os.makedirs(root, exist_ok=True)
    bundle = Bundle(root, backend)
    calendar = bundle.calendar

    daily_sids = range(DAILY_SIDS)
    # Minute assets are a subset of the daily ones, as in real bundles.
    minute_sids = range(MINUTE_SIDS)

    asset_writer = AssetDBWriter(bundle.assets_path)
    asset_writer.write(
        equities=equity_info(daily_sids, DAILY_START, DAILY_END),
        exchanges=pd.DataFrame(
            {"exchange": ["NYSE"], "canonical_name": ["NYSE"], "country_code": ["US"]}
        ),
    )
    asset_writer.engine.dispose()

    write_daily(
        backend,
        bundle.daily_path,
        calendar,
        daily_frames(calendar, daily_sids, DAILY_START, DAILY_END),
        DAILY_START,
        DAILY_END,
    )
    write_minute(
        backend,
        bundle.minute_path,
        calendar,
        minute_frames(calendar, minute_sids, MINUTE_START, MINUTE_END),
        MINUTE_START,
        MINUTE_END,
    )

    sessions = calendar.sessions_in_range(DAILY_START, DAILY_END)
    empty = pd.DataFrame(
        {
            "sid": np.array([], dtype="int64"),
            "effective_date": np.array([], dtype="int64"),
            "ratio": np.array([], dtype="float64"),
        }
    )
    with SQLiteAdjustmentWriter(
        bundle.adjustments_path, bundle.daily_reader(), overwrite=True
    ) as writer:
        writer.write(splits=splits(daily_sids, sessions), mergers=empty)
    return bundle
