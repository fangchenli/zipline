"""Types that bundle authors and users get from zipline.data.bundles, checked
by ty.

Like ``api_types.py``, this module isn't run: CI type-checks it.
"""

from collections.abc import Mapping
from typing import assert_type

import pandas as pd

from zipline.assets import AssetDBWriter, AssetFinder
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
