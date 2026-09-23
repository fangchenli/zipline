import numpy as np
import pandas as pd


def as_utc(dts):
    """Coerce requested ``dts`` to a UTC ``DatetimeIndex``.

    FX rates are stored as UTC points in time. Session labels are tz-naive, so
    naive inputs are interpreted as UTC (i.e. midnight UTC for sessions).
    """
    dts = pd.DatetimeIndex(dts)
    if dts.tz is None:
        return dts.tz_localize("UTC")
    return dts.tz_convert("UTC")


def check_dts(requested_dts):
    """Validate that ``requested_dts`` are valid for querying from an FX reader."""
    if not is_sorted_ascending(requested_dts):
        raise ValueError("Requested fx rates with non-ascending dts.")


def is_sorted_ascending(array):
    return (np.maximum.accumulate(array) <= array).all()
