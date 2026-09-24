"""Types that user algorithms get from zipline's API, checked by ty.

This module isn't run: CI type-checks it (``uv run ty check tests/typing``),
and each ``assert_type`` fails if an annotation or overload changes what an
algorithm sees.
"""

from typing import Literal, assert_type

import pandas as pd
from pandas.api.typing import NaTType

from zipline.algorithm import TradingAlgorithm
from zipline.api import (
    get_environment,
    get_open_orders,
    get_order,
    order,
    order_target_percent,
    record,
    schedule_function,
    symbol,
    symbols,
)
from zipline.assets import Asset, Equity
from zipline.protocol import BarData, Order


def handle_data(context: TradingAlgorithm, data: BarData) -> None:
    aapl = symbol("AAPL")
    assert_type(aapl, Equity)
    assets = symbols("AAPL", "MSFT", country_code="US")
    assert_type(assets, list[Equity])

    # Asset attributes. Assets built without dates (e.g. in tests) have None.
    assert_type(aapl.sid, int)
    assert_type(aapl.symbol, str)
    assert_type(aapl.exchange, str)
    assert_type(aapl.end_date, pd.Timestamp | None)
    assert_type(aapl < 10, bool)

    # BarData.current: a scalar, a Series or a DataFrame, by argument shape.
    assert_type(data.current(aapl, "price"), float)
    assert_type(data.current(aapl, "volume"), float)
    assert_type(data.current(aapl, "last_traded"), pd.Timestamp | NaTType)
    assert_type(data.current(aapl, ["open", "close"]), pd.Series[float | pd.Timestamp])
    assert_type(data.current(assets, "price"), pd.Series[float])
    assert_type(data.current(assets, "last_traded"), pd.Series[pd.Timestamp])
    assert_type(data.current(assets, ["open", "close"]), pd.DataFrame)

    # BarData.history: a Series for one asset and field, else a DataFrame.
    assert_type(data.history(aapl, "close", 20, "1d"), pd.Series[float])
    assert_type(data.history(aapl, ["open", "close"], 20, "1d"), pd.DataFrame)
    assert_type(data.history(assets, "close", 20, "1m"), pd.DataFrame)

    assert_type(data.can_trade(aapl), bool)
    assert_type(data.can_trade(assets), pd.Series[bool])
    assert_type(data.is_stale(assets), pd.Series[bool])
    assert_type(data.current_dt, pd.Timestamp)

    order_id = order(aapl, 10)
    assert_type(order_id, str | None)
    assert_type(order_target_percent(aapl, 0.5), str | None)
    if order_id is not None:
        placed = get_order(order_id)
        assert_type(placed, Order | None)
        if placed is not None:
            assert_type(placed.filled, int)
            assert_type(placed.sid, Asset)
    assert_type(get_open_orders(), dict[Asset, list[Order]] | list[Order])

    assert_type(get_environment(), str)
    assert_type(get_environment("start"), pd.Timestamp)
    assert_type(get_environment("data_frequency"), Literal["daily", "minute"])
    assert_type(get_environment("capital_base"), float)
    record(price=data.current(aapl, "price"))


def initialize(context: TradingAlgorithm) -> None:
    schedule_function(handle_data)
