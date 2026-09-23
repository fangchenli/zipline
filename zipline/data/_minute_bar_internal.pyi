from typing import Any

from numpy import ndarray

def find_last_traded_position_internal(
    market_opens: ndarray,
    market_closes: ndarray,
    end_minute: int,
    start_minute: int,
    volumes,
    minutes_per_day: int,
) -> Any: ...
def find_position_of_minute(
    market_opens: ndarray,
    market_closes: ndarray,
    minute_val: int,
    minutes_per_day: int,
    forward_fill: bool,
) -> Any: ...
def minute_value(market_opens: ndarray, pos: int, minutes_per_day: int) -> Any: ...
