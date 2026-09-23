import pandas as pd
from toolz import partition_all


def make_utc_aware(dti):
    """Return ``dti`` localized to UTC if it is tz-naive, otherwise
    converted to UTC.
    """
    if dti.tz is None:
        return dti.tz_localize("UTC")
    return dti.tz_convert("UTC")


def to_session_label(dt):
    """Coerce a date-like value to a session label: a tz-naive midnight
    ``pd.Timestamp``.

    tz-aware values are converted to UTC before the timezone is dropped, so
    a UTC minute maps to its UTC calendar date. The result has nanosecond
    resolution, like calendar sessions.
    """
    dt = pd.Timestamp(dt)
    if not isinstance(dt, pd.Timestamp):
        return dt  # NaT
    if dt.tz is not None:
        dt = dt.tz_convert("UTC").tz_localize(None)
    return dt.normalize().as_unit("ns")


def to_session_labels(dts):
    """Vectorized :func:`to_session_label` for a ``pd.DatetimeIndex``."""
    dts = pd.DatetimeIndex(dts)
    if dts.tz is not None:
        dts = dts.tz_convert("UTC").tz_localize(None)
    return dts.normalize().as_unit("ns")


def compute_date_range_chunks(sessions, start_date, end_date, chunksize):
    """Compute the start and end dates to run a pipeline for.

    Parameters
    ----------
    sessions : DatetimeIndex
        The available dates.
    start_date : pd.Timestamp
        The first date in the pipeline.
    end_date : pd.Timestamp
        The last date in the pipeline.
    chunksize : int or None
        The size of the chunks to run. Setting this to None returns one chunk.

    Returns
    -------
    ranges : iterable[(np.datetime64, np.datetime64)]
        A sequence of start and end dates to run the pipeline for.
    """
    if start_date not in sessions:
        raise KeyError(
            "Start date {} is not found in calendar.".format(
                start_date.strftime("%Y-%m-%d")
            )
        )
    if end_date not in sessions:
        raise KeyError(
            "End date {} is not found in calendar.".format(
                end_date.strftime("%Y-%m-%d")
            )
        )
    if end_date < start_date:
        raise ValueError(
            "End date {} cannot precede start date {}.".format(
                end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")
            )
        )

    if chunksize is None:
        return [(start_date, end_date)]

    start_ix, end_ix = sessions.slice_locs(start_date, end_date)
    return ((r[0], r[-1]) for r in partition_all(chunksize, sessions[start_ix:end_ix]))
