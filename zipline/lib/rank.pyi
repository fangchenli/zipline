from typing import Any

from numpy import ndarray

def grouped_masked_is_maximal(
    data: ndarray, groupby: ndarray, mask: ndarray
) -> Any: ...
def masked_rankdata_2d(
    data: ndarray, mask: ndarray, missing_value, method: str, ascending: bool
) -> Any: ...

nan: float

def rankdata_1d_descending(data: ndarray, method: str) -> Any: ...
def rankdata_2d_ordinal(array: ndarray) -> Any: ...
