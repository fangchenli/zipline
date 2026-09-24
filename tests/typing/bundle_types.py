"""Types that bundle authors and users get from zipline.data.bundles, checked
by ty.

Like ``api_types.py``, this module isn't run: CI type-checks it.
"""

from collections.abc import Mapping
from typing import Literal, assert_type

import pandas as pd

from zipline.assets import (
    Asset,
    AssetDBWriter,
    AssetFinder,
    ContinuousFuture,
    Equity,
    Future,
)
from zipline.data.adjustments import SQLiteAdjustmentReader, SQLiteAdjustmentWriter
from zipline.data.bundles import (
    bundles,
    clean,
    ingest,
    ingestions_for_bundle,
    load,
    register,
)
from zipline.data.bundles.core import BundleData, RegisteredBundle
from zipline.data.data_portal import DataPortal, SpotValue
from zipline.data.minute_bars import MinuteBarReader
from zipline.data.parquet_daily_bars import ParquetDailyBarWriter
from zipline.data.parquet_minute_bars import ParquetMinuteBarWriter
from zipline.utils.cache import dataframe_cache
from zipline.utils.calendar_utils import ExchangeCalendar


# register(name) is a decorator that keeps the ingest function's type.
@register("my-bundle", calendar_name="XNYS")
def my_bundle(
    environ: Mapping[str, str],
    asset_db_writer: AssetDBWriter,
    minute_bar_writer: ParquetMinuteBarWriter,
    daily_bar_writer: ParquetDailyBarWriter,
    adjustment_writer: SQLiteAdjustmentWriter,
    calendar: ExchangeCalendar,
    start_session: pd.Timestamp,
    end_session: pd.Timestamp,
    cache: dataframe_cache,
    show_progress: bool,
    output_dir: str,
) -> None:
    daily_bar_writer.write(())


# Without writers, the ingest function gets None for each of them.
@register("prebuilt", create_writers=False)
def prebuilt(
    environ: Mapping[str, str],
    asset_db_writer: None,
    minute_bar_writer: None,
    daily_bar_writer: None,
    adjustment_writer: None,
    calendar: ExchangeCalendar,
    start_session: pd.Timestamp,
    end_session: pd.Timestamp,
    cache: dataframe_cache,
    show_progress: bool,
    output_dir: str,
) -> None:
    pass


def use_bundle() -> None:
    assert_type(bundles["my-bundle"], RegisteredBundle)
    ingest("my-bundle", show_progress=True)
    with load("my-bundle") as data:
        assert_type(data, BundleData)
        assert_type(data.asset_finder, AssetFinder)
        assert_type(data.equity_minute_bar_reader, MinuteBarReader)
        assert_type(data.adjustment_reader, SQLiteAdjustmentReader)
    assert_type(ingestions_for_bundle("my-bundle"), list[pd.Timestamp])
    assert_type(clean("my-bundle", keep_last=1), set[str])


def use_asset_finder(finder: AssetFinder, session: pd.Timestamp) -> None:
    aapl = finder.lookup_symbol("AAPL", as_of_date=session)
    assert_type(aapl, Equity)
    assert_type(finder.lookup_symbols(["AAPL", "MSFT"], session), list[Equity])
    assert_type(finder.lookup_future_symbol("CLF16"), Future)
    # A sid may belong to a continuous future made by create_continuous_future.
    assert_type(finder.retrieve_asset(24), Asset | ContinuousFuture)
    assert_type(
        finder.retrieve_asset(24, default_none=True), Asset | ContinuousFuture | None
    )
    assert_type(finder.retrieve_equities([24]), dict[int, Equity])
    cl = finder.create_continuous_future("CL", 0, "calendar", "mul")
    assert_type(cl, ContinuousFuture)
    assert_type(cl.roll_style, Literal["calendar", "volume"])
    assert_type(
        finder.lookup_generic("AAPL", session, "US")[0], Asset | ContinuousFuture
    )
    assert_type(finder.sids, tuple[int, ...])


def use_data_portal(
    data: BundleData, calendar: ExchangeCalendar, aapl: Equity, now: pd.Timestamp
) -> None:
    portal = DataPortal(
        data.asset_finder,
        calendar,
        data.equity_daily_bar_reader.first_trading_day,
        equity_daily_reader=data.equity_daily_bar_reader,
        equity_minute_reader=data.equity_minute_bar_reader,
        adjustment_reader=data.adjustment_reader,
    )
    history = portal.get_history_window([aapl], now, 20, "1d", "close", "daily")
    assert_type(history, pd.DataFrame)
    assert_type(portal.get_spot_value([aapl], "close", now, "daily"), list[SpotValue])
    assert_type(portal.get_splits([aapl], now), list[tuple[Asset, float]])
    dividends = data.adjustment_reader.get_dividends_with_ex_date(
        [aapl.sid], now, data.asset_finder
    )
    assert_type(dividends[0].pay_date, pd.Timestamp)
