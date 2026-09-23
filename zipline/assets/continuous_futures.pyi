from typing import Any

from numpy import ndarray

ADJUSTMENT_STYLES: set
CHAIN_PREDICATES: dict

class ContinuousFuture:
    def __eq__(self, value, /) -> Any: ...
    def __ge__(self, value, /) -> Any: ...
    def __gt__(self, value, /) -> Any: ...
    def __hash__(self, /) -> Any: ...
    def __init__(
        self,
        sid: int,
        root_symbol,
        offset: int,
        roll_style,
        start_date,
        end_date,
        exchange_info,
        adjustment=None,
    ) -> None: ...
    def __le__(self, value, /) -> Any: ...
    def __lt__(self, value, /) -> Any: ...
    def __ne__(self, value, /) -> Any: ...
    _kwargnames: Any
    adjustment: Any
    end_date: Any
    exchange: Any
    exchange_full: Any
    exchange_info: Any
    @classmethod
    def from_dict(cls, dict_) -> Any: ...
    def is_alive_for_session(self, session_label) -> Any: ...
    def is_exchange_open(self, dt_minute) -> Any: ...
    offset: int
    roll_style: Any
    root_symbol: Any
    sid: int
    start_date: Any
    def to_dict(self) -> Any: ...

class ContractNode:
    def __init__(self, contract) -> None: ...
    contract: Any
    next: Any
    prev: Any

class OrderedContracts:
    def __init__(self, root_symbol, contracts, chain_predicate=None) -> None: ...
    _end_date: int
    _head_contract: Any
    _start_date: int
    def active_chain(self, starting_sid: int, dt_value: int) -> ndarray: ...
    chain_predicate: Any
    def contract_at_offset(self, sid: int, offset: int, start_cap: int) -> Any: ...
    def contract_before_auto_close(self, dt_value: int) -> int: ...
    end_date: Any
    root_symbol: Any
    sid_to_contract: dict
    start_date: Any

def delivery_predicate(codes, contract) -> Any: ...
