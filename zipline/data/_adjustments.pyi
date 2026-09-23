from typing import Any

from pandas import DatetimeIndex, Index

ADJ_QUERY_TEMPLATE: str
SQLITE_MAX_IN_STATEMENT: int

def load_adjustments_from_sqlite(
    adjustments_db,
    dates: DatetimeIndex,
    assets: Index,
    should_include_splits: bool,
    should_include_mergers: bool,
    should_include_dividends: bool,
    adjustment_type: str,
) -> Any: ...
