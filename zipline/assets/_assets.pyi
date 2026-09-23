from typing import Any

class Asset:
    def __eq__(self, value, /) -> Any: ...
    def __ge__(self, value, /) -> Any: ...
    def __gt__(self, value, /) -> Any: ...
    def __hash__(self, /) -> Any: ...
    def __init__(
        self,
        sid: int,
        exchange_info,
        symbol="",
        asset_name="",
        start_date=None,
        end_date=None,
        first_traded=None,
        auto_close_date=None,
        tick_size=0.01,
        multiplier: float = 1.0,
    ) -> None: ...
    def __le__(self, value, /) -> Any: ...
    def __lt__(self, value, /) -> Any: ...
    def __ne__(self, value, /) -> Any: ...
    _kwargnames: Any
    asset_name: Any
    auto_close_date: Any
    country_code: Any
    end_date: Any
    exchange: Any
    exchange_full: Any
    exchange_info: Any
    first_traded: Any
    @classmethod
    def from_dict(cls, dict_) -> Any: ...
    def is_alive_for_session(self, session_label) -> Any: ...
    def is_exchange_open(self, dt_minute) -> Any: ...
    price_multiplier: float
    sid: int
    start_date: Any
    symbol: Any
    tick_size: Any
    def to_dict(self) -> Any: ...

class Equity(Asset):
    security_end_date: Any
    security_name: Any
    security_start_date: Any

class Future(Asset):
    def __init__(
        self,
        sid: int,
        exchange_info,
        symbol="",
        root_symbol="",
        asset_name="",
        start_date=None,
        end_date=None,
        notice_date=None,
        expiration_date=None,
        auto_close_date=None,
        first_traded=None,
        tick_size=0.001,
        multiplier: float = 1.0,
    ) -> None: ...
    _kwargnames: Any
    expiration_date: Any
    multiplier: Any
    notice_date: Any
    root_symbol: Any
    def to_dict(self) -> Any: ...

def make_asset_array(size: int, asset: Asset) -> Any: ...
