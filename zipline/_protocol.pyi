from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from typing import Literal, TypeVar, overload

import pandas as pd
from pandas.api.typing import NaTType

from zipline.assets import Asset, ContinuousFuture, Future
from zipline.data.data_portal import DataPortal
from zipline.finance.asset_restrictions import Restrictions
from zipline.utils.calendar_utils import ExchangeCalendar

#: The fields whose current value is a number.
NumericField = Literal["price", "open", "high", "low", "close", "volume"]

_F = TypeVar("_F", bound=Callable[..., object])

class BarData:
    """Current and historical prices and volumes, passed to algorithms as
    ``data``."""

    def __init__(
        self,
        data_portal: DataPortal,
        simulation_dt_func: Callable[[], pd.Timestamp],
        data_frequency: Literal["daily", "minute"],
        trading_calendar: ExchangeCalendar,
        restrictions: Restrictions,
        universe_func: Callable[[], Iterable[Asset]] | None = None,
    ) -> None: ...

    # A single asset and field give a scalar; a single asset or field and
    # several of the other a Series; several of both a DataFrame. Fields given
    # as plain strings rather than literals can be any field.
    @overload
    def current(self, assets: Asset, fields: NumericField) -> float: ...
    @overload
    def current(
        self, assets: Asset, fields: Literal["last_traded"]
    ) -> pd.Timestamp | NaTType: ...
    @overload
    def current(self, assets: Asset, fields: str) -> float | pd.Timestamp | NaTType: ...
    @overload
    def current(
        self, assets: Asset, fields: Iterable[str]
    ) -> pd.Series[float | pd.Timestamp]: ...
    @overload
    def current(
        self, assets: Iterable[Asset], fields: NumericField
    ) -> pd.Series[float]: ...
    @overload
    def current(
        self, assets: Iterable[Asset], fields: Literal["last_traded"]
    ) -> pd.Series[pd.Timestamp]: ...
    @overload
    def current(
        self, assets: Iterable[Asset], fields: str
    ) -> pd.Series[float | pd.Timestamp]: ...
    @overload
    def current(
        self, assets: Iterable[Asset], fields: Iterable[str]
    ) -> pd.DataFrame: ...

    # A single asset and field give a Series; anything else a DataFrame.
    @overload
    def history(
        self,
        assets: Asset,
        fields: NumericField,
        bar_count: int,
        frequency: Literal["1m", "1d"],
    ) -> pd.Series[float]: ...
    @overload
    def history(
        self,
        assets: Asset,
        fields: Literal["last_traded"],
        bar_count: int,
        frequency: Literal["1m", "1d"],
    ) -> pd.Series[pd.Timestamp]: ...
    @overload
    def history(
        self,
        assets: Asset,
        fields: str,
        bar_count: int,
        frequency: Literal["1m", "1d"],
    ) -> pd.Series[float | pd.Timestamp]: ...
    @overload
    def history(
        self,
        assets: Asset | Iterable[Asset],
        fields: str | Iterable[str],
        bar_count: int,
        frequency: Literal["1m", "1d"],
    ) -> pd.DataFrame: ...
    @overload
    def can_trade(self, assets: Asset) -> bool: ...
    @overload
    def can_trade(self, assets: Iterable[Asset]) -> pd.Series[bool]: ...
    @overload
    def is_stale(self, assets: Asset) -> bool: ...
    @overload
    def is_stale(self, assets: Iterable[Asset]) -> pd.Series[bool]: ...
    def current_chain(self, continuous_future: ContinuousFuture) -> list[Future]: ...
    @property
    def current_dt(self) -> pd.Timestamp: ...
    @property
    def current_session(self) -> pd.Timestamp: ...
    @property
    def current_session_minutes(self) -> pd.DatetimeIndex: ...
    @property
    def fetcher_assets(self) -> list[Asset]: ...

    # Only set, by handle_non_market_minutes.
    _handle_non_market_minutes: bool

    # Deprecated: the algorithm's universe of assets as a mapping.
    def __iter__(self) -> Iterator[Asset]: ...
    def __contains__(self, asset: object, /) -> bool: ...
    def __len__(self) -> int: ...
    def __getitem__(self, name: Asset, /) -> SidView: ...
    def items(self) -> list[tuple[Asset, SidView]]: ...
    def keys(self) -> list[Asset]: ...
    def iterkeys(self) -> Iterator[Asset]: ...

class InnerPosition:
    def __init__(
        self,
        asset: Asset,
        amount: int = 0,
        cost_basis: float = 0.0,
        last_sale_price: float = 0.0,
        last_sale_date: pd.Timestamp | None = None,
    ) -> None: ...
    asset: Asset
    amount: int
    cost_basis: float
    last_sale_price: float
    last_sale_date: pd.Timestamp | None

class SidView:
    """Deprecated: one asset's current values, from ``data[asset]``."""

    def __init__(
        self,
        asset: Asset,
        data_portal: DataPortal,
        simulation_dt_func: Callable[[], pd.Timestamp],
        data_frequency: Literal["daily", "minute"],
    ) -> None: ...
    def __getattr__(self, column: str) -> float | pd.Timestamp | NaTType: ...
    def __getitem__(self, column: str, /) -> float | pd.Timestamp | NaTType: ...
    def __contains__(self, column: str, /) -> bool: ...
    @property
    def sid(self) -> Asset: ...
    @property
    def dt(self) -> pd.Timestamp: ...
    @property
    def datetime(self) -> pd.Timestamp | NaTType: ...
    @property
    def current_dt(self) -> pd.Timestamp: ...
    def mavg(self, num_minutes: int) -> float: ...
    def stddev(self, num_minutes: int) -> float: ...
    def vwap(self, num_minutes: int) -> float: ...
    def returns(self) -> float: ...

class check_parameters:
    """Decorator checking the types of a function's keyword arguments."""

    def __init__(
        self, keyword_names: tuple[str, ...], types: tuple[type, ...]
    ) -> None: ...
    def __call__(self, func: _F) -> _F: ...

def handle_non_market_minutes(
    bar_data: BarData,
) -> AbstractContextManager[None]: ...
