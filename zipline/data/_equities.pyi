from typing import Any

from numpy import ndarray
from pandas import Index

def _compute_row_slices(
    asset_starts_absolute: dict,
    asset_ends_absolute: dict,
    asset_starts_calendar: dict,
    query_start: int,
    query_end: int,
    requested_assets: Index,
) -> Any: ...
def _read_bcolz_data(
    table: Any,
    shape: tuple,
    columns: list,
    first_rows: ndarray,
    last_rows: ndarray,
    offsets: ndarray,
    read_all: bool,
) -> Any: ...
