from enum import IntEnum
from typing import Any

from numpy import ndarray
from pandas import DatetimeIndex, Index, Timestamp

ADJUSTMENT_KIND_NAMES: dict

class Adjustment:
    def __eq__(self, value, /) -> Any: ...
    def __ge__(self, value, /) -> Any: ...
    def __gt__(self, value, /) -> Any: ...
    __hash__: Any
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int
    ) -> None: ...
    def __le__(self, value, /) -> Any: ...
    def __lt__(self, value, /) -> Any: ...
    def __ne__(self, value, /) -> Any: ...
    def _key(self) -> tuple: ...
    first_col: int
    first_row: int
    @classmethod
    def from_assets_and_dates(
        cls, dates_index, assets_index, start_date, end_date, asset_id, value
    ) -> Any: ...
    last_col: int
    last_row: int

class AdjustmentKind(IntEnum):
    MULTIPLY = 0
    ADD = 1
    OVERWRITE = 2

# Re-exported at module level (see the end of adjustment.pyx).
MULTIPLY = AdjustmentKind.MULTIPLY
ADD = AdjustmentKind.ADD
OVERWRITE = AdjustmentKind.OVERWRITE

class ArrayAdjustment(Adjustment): ...

class Boolean1DArrayOverwrite(ArrayAdjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, values
    ) -> None: ...
    def mutate(self, data: ndarray) -> Any: ...
    values: ndarray

class BooleanAdjustment(Adjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, value: bool
    ) -> None: ...
    value: int

class BooleanOverwrite(BooleanAdjustment):
    def mutate(self, data: ndarray) -> Any: ...

class Datetime641DArrayOverwrite(ArrayAdjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, values
    ) -> None: ...
    def mutate(self, data: ndarray) -> Any: ...
    values: ndarray

class Datetime64Adjustment(_Int64Adjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, value
    ) -> None: ...

class Datetime64Overwrite(Datetime64Adjustment):
    def mutate(self, data: ndarray) -> Any: ...

class Float641DArrayOverwrite(ArrayAdjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, values
    ) -> None: ...
    def mutate(self, data: ndarray) -> Any: ...
    values: ndarray

class Float64Add(Float64Adjustment):
    def mutate(self, data: ndarray) -> Any: ...

class Float64Adjustment(Adjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, value: float
    ) -> None: ...
    @classmethod
    def from_assets_and_dates(
        cls, dates_index, assets_index, start_date, end_date, asset_id, value
    ) -> Any: ...
    value: int

class Float64Multiply(Float64Adjustment):
    def mutate(self, data: ndarray) -> Any: ...

class Float64Overwrite(Float64Adjustment):
    def mutate(self, data: ndarray) -> Any: ...

class Int64Overwrite(_Int64Adjustment):
    def mutate(self, data: ndarray) -> Any: ...

class Object1DArrayOverwrite(ArrayAdjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, values
    ) -> None: ...
    def mutate(self, data) -> Any: ...
    values: ndarray

class ObjectOverwrite(_ObjectAdjustment):
    def mutate(self, data) -> Any: ...

class _Int64Adjustment(Adjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, value: int
    ) -> None: ...
    value: int

class _ObjectAdjustment(Adjustment):
    def __init__(
        self, first_row: int, last_row: int, first_col: int, last_col: int, value
    ) -> None: ...
    value: int

def _from_assets_and_dates(
    cls,
    dates_index: DatetimeIndex,
    assets_index: Index,
    start_date: Timestamp,
    end_date: Timestamp,
    asset_id: int,
    value,
) -> Any: ...
def choose_adjustment_type(adjustment_kind: AdjustmentKind, value) -> Any: ...
def get_adjustment_locs(
    dates_index: DatetimeIndex,
    assets_index: Index,
    start_date: Timestamp,
    end_date: Timestamp,
    asset_id: int,
) -> tuple: ...
def make_adjustment_from_indices(
    first_row: int,
    last_row: int,
    first_column: int,
    last_column: int,
    adjustment_kind: AdjustmentKind,
    value,
) -> Adjustment: ...
def make_adjustment_from_labels(
    dates_index: DatetimeIndex,
    assets_index: Index,
    start_date: Timestamp,
    end_date: Timestamp,
    asset_id: int,
    adjustment_kind: AdjustmentKind,
    value,
) -> Any: ...
