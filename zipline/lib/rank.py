"""
Functions for ranking and sorting.
"""

import numpy as np
from scipy.stats import rankdata

from zipline.utils.numpy_utils import is_missing

_RANKABLE_KINDS = {"f", "i", "M"}


def _keys(data):
    """Floats ordered like ``data``, with NaN for missing values.

    Floats are their own keys. Integers and datetimes can be too large to be
    exact as floats, so their keys are their positions among the distinct
    values.
    """
    if data.dtype.kind not in _RANKABLE_KINDS:
        raise TypeError(
            f"Can't compute rankdata on array of dtype {data.dtype.name!r}."
        )
    if data.dtype.kind == "f":
        return data.astype(np.float64, copy=True)
    _, positions = np.unique(data, return_inverse=True)
    keys = positions.reshape(data.shape).astype(np.float64)
    if data.dtype.kind == "M":
        keys[np.isnat(data)] = np.nan
    return keys


def rankdata_1d_ascending(data, method):
    """1D :func:`scipy.stats.rankdata` that gives missing values (NaN or NaT)
    a rank of NaN.
    """
    return rankdata(_keys(data), method=method, nan_policy="omit")


def rankdata_1d_descending(data, method):
    """Descending version of :func:`rankdata_1d_ascending`."""
    return rankdata(-_keys(data), method=method, nan_policy="omit")


def masked_rankdata_2d(data, mask, missing_value, method, ascending):
    """Rank each row of float64, int64 or datetime64 ``data``.

    Locations that are masked out or hold ``missing_value`` get a rank of NaN,
    and aren't counted in the ranks of the others.
    """
    keys = _keys(data)
    keys[~mask | is_missing(data, missing_value)] = np.nan
    if not ascending:
        keys = -keys
    return rankdata(keys, method=method, axis=1, nan_policy="omit")


def grouped_masked_is_maximal(data, groupby, mask):
    """Build a mask of the top value for each row in ``data``, grouped by
    ``groupby`` and masked by ``mask``.

    Parameters
    ----------
    data : np.array
        Data on which we should find maximal values for each row.
    groupby : np.array[int64_t]
        Grouping labels for rows of ``data``. We choose one entry in each
        row for each unique grouping key in that row.
    mask : np.array[bool]
        Boolean mask of locations to consider as possible maximal values.
        Callers are responsible for masking out missing values.

    Returns
    -------
    maximal_locations : np.array[bool]
        Mask containing True for the maximal non-masked value in each row and
        group; of equal values, the first.
    """
    if not data.shape == groupby.shape == mask.shape:
        raise AssertionError(
            "Misaligned shapes in grouped_masked_is_maximal:"
            f"data={data.shape}, groupby={groupby.shape}, mask={mask.shape}"
        )
    rows, cols = np.nonzero(mask)
    out = np.zeros(mask.shape, dtype=bool)
    if not len(rows):
        return out
    values = data[rows, cols]
    # Number each (row, group) pair.
    _, group_codes = np.unique(groupby[rows, cols], return_inverse=True)
    pairs = rows * (group_codes.max() + 1) + group_codes

    maxima = np.full(pairs.max() + 1, values.min())
    np.maximum.at(maxima, pairs, values)
    is_maximal = values == maxima[pairs]
    # np.nonzero lists locations in row-major order, so the first maximal
    # location of each pair has the lowest column.
    _, first = np.unique(pairs[is_maximal], return_index=True)
    out[rows[is_maximal][first], cols[is_maximal][first]] = True
    return out
