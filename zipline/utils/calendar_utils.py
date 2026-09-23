"""
Zipline's interface to exchange_calendars.

All calendar access in zipline should go through this module so that
calendars are constructed with zipline's conventions:

- ``side="right"``: a session's trading minutes run from one minute after the
  open through the close, inclusive (e.g. 9:31 to 16:00 for XNYS). Every
  minute is labelled by the close of the bar it represents.
- History extends back to :data:`DEFAULT_START` (or the calendar's earliest
  supported date, if later), so backtests over old data work out of the box.

Session labels are timezone-naive midnight timestamps; minutes are
timezone-aware UTC timestamps.
"""
import pandas as pd
from exchange_calendars import (
    ExchangeCalendar,
    clear_calendars,
    deregister_calendar,
    register_calendar,
    register_calendar_alias,
    register_calendar_type,
)
from exchange_calendars import get_calendar as _xc_get_calendar
from exchange_calendars.calendar_utils import global_calendar_dispatcher

__all__ = [
    "DEFAULT_START",
    "ExchangeCalendar",
    "clear_calendars",
    "days_at_time",
    "deregister_calendar",
    "execution_time_from_close",
    "execution_time_from_open",
    "get_calendar",
    "register_calendar",
    "register_calendar_alias",
    "register_calendar_type",
]

DEFAULT_START = pd.Timestamp("1990-01-01")
SIDE = "right"


def get_calendar(name, start=None, end=None, side=SIDE):
    """Get the exchange calendar registered as ``name``.

    Calendars registered as factories (all built-in exchange_calendars
    calendars) are built with zipline's defaults. Calendars registered as
    instances with :func:`register_calendar` are returned as-is.
    """
    dispatcher = global_calendar_dispatcher
    resolved = dispatcher.resolve_alias(name)
    if resolved in dispatcher._calendars:
        return dispatcher._calendars[resolved]

    if start is None:
        factory = dispatcher._calendar_factories.get(resolved)
        bound_min = factory.bound_min() if factory is not None else None
        start = DEFAULT_START
        if bound_min is not None and bound_min > start:
            start = bound_min

    return _xc_get_calendar(resolved, start=start, end=end, side=side)


def execution_time_from_open(calendar, opens):
    """Map session opens to the first minute at which orders may execute.

    This is the identity for every calendar except ``us_futures``, whose
    execution window starts some hours after the (overnight) open.
    """
    f = getattr(calendar, "execution_time_from_open", None)
    return opens if f is None else f(opens)


def execution_time_from_close(calendar, closes):
    """Map session closes to the last minute at which orders may execute.

    This is the identity for every calendar except ``us_futures``, whose
    execution window ends before the session close.
    """
    f = getattr(calendar, "execution_time_from_close", None)
    return closes if f is None else f(closes)


def days_at_time(days, t, tz, day_offset=0):
    """
    Create an index of days at time ``t``, interpreted in timezone ``tz``.

    The returned index is localized to UTC.

    Parameters
    ----------
    days : DatetimeIndex
        An index of dates (represented as midnight).
    t : datetime.time
        The time to apply as an offset to each day in ``days``.
    tz : str or tzinfo
        The timezone to use to interpret ``t``.
    day_offset : int
        The number of days we want to offset @days by

    Examples
    --------
    In the example below, the times switch from 13:45 to 12:45 UTC because
    March 13th is the daylight savings transition for America/New_York. All
    the times are still 8:45 when interpreted in America/New_York.

    >>> import pandas as pd; import datetime; import pprint
    >>> dts = pd.date_range('2016-03-12', '2016-03-14')
    >>> dts_845 = days_at_time(dts, datetime.time(8, 45), 'America/New_York')
    >>> pprint.pprint([str(dt) for dt in dts_845])
    ['2016-03-12 13:45:00+00:00',
     '2016-03-13 12:45:00+00:00',
     '2016-03-14 12:45:00+00:00']
    """
    days = pd.DatetimeIndex(days).tz_localize(None)
    if len(days) == 0:
        return days.tz_localize("UTC")

    # Offset days without tz to avoid timezone issues.
    delta = pd.Timedelta(
        days=day_offset,
        hours=t.hour,
        minutes=t.minute,
        seconds=t.second,
    )
    return (days + delta).tz_localize(tz).tz_convert("UTC")
