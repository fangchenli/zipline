from typing import Any

from numpy import ndarray

class PositionStats:
    gross_exposure: float
    gross_value: float
    long_exposure: float
    long_value: float
    longs_count: int
    net_exposure: float
    net_value: float
    @classmethod
    def new(cls) -> Any: ...
    position_exposure_array: Any
    position_exposure_series: Any
    short_exposure: float
    short_value: float
    shorts_count: int

def calculate_position_tracker_stats(positions, stats: PositionStats) -> Any: ...
def minute_annual_volatility(
    date_labels: ndarray, minute_returns: ndarray, daily_returns: ndarray
) -> Any: ...
def update_position_last_sale_prices(positions, get_price, dt) -> Any: ...
