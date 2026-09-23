from typing import Any

BAR: int
BEFORE_TRADING_START_BAR: int
MINUTE_END: int

class MinuteSimulationClock:
    def __init__(
        self,
        sessions,
        market_opens,
        market_closes,
        before_trading_start_minutes,
        minute_emission=False,
    ) -> None: ...
    def __iter__(self, /) -> Any: ...
    def _get_minutes_for_list(self, minutes, minute_emission) -> Any: ...

NANOS_IN_MINUTE: int
SESSION_END: int
SESSION_START: int

def _as_nanos(dts) -> Any: ...
