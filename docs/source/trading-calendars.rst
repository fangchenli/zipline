Trading Calendars
-----------------

What is a Trading Calendar?
~~~~~~~~~~~~~~~~~~~~~~~~~~~
A trading calendar represents the timing information of a single market exchange. The timing information is made up of two parts: sessions, and opens/closes. Zipline uses the calendars from the `exchange_calendars <https://github.com/gerrymanoim/exchange_calendars>`__ library, which are subclasses of :class:`exchange_calendars.ExchangeCalendar`.

A session represents a contiguous set of minutes. Its label is a **timezone-naive** midnight timestamp for the session's date, for example ``pd.Timestamp("2024-01-03")``. A session label is not a specific point in time. Minutes, on the other hand, are timezone-aware UTC timestamps.

For an average day of the `New York Stock Exchange <https://www.nyse.com/index>`__, the market opens at 9:30AM and closes at 4PM. Trading sessions can change depending on the exchange, day of the year, etc.


Why Should You Care About Trading Calendars?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Let's say you want to buy a share of some equity on Tuesday, and then sell it on Saturday. If the exchange in which you're trading that equity is not open on Saturday, then in reality it would not be possible to trade that equity at that time, and you would have to wait until some other number of days past Saturday. Since you wouldn't be able to place the trade in reality, it would also be unreasonable for your backtest to place a trade on Saturday.

In order for you to backtest your strategy, the dates that are accounted for in your :doc:`data bundle <bundles>` and the dates in your trading calendar should match up; if the dates don't match up, then you're going to see some errors along the way. This holds for both minutely and daily data.


Getting a Calendar
~~~~~~~~~~~~~~~~~~

Always get calendars through :func:`zipline.utils.calendar_utils.get_calendar` (also available as ``zipline.get_calendar``) rather than from ``exchange_calendars`` directly, because it builds calendars with Zipline's conventions:

- ``side="right"``: a session's trading minutes run from one minute after the open through the close, inclusive, so each minute is labelled by the close of the bar it represents. For the NYSE that is 9:31 to 16:00.
- History extends back to 1990 (or the calendar's earliest supported date, if later).

.. code-block:: python

  import pandas as pd
  from zipline.utils.calendar_utils import get_calendar

  nyse = get_calendar("XNYS")
  sessions = nyse.sessions_in_range("2024-01-02", "2024-01-05")
  nyse.session_first_minute(sessions[0])  # 2024-01-02 14:31:00+00:00
  nyse.session_last_minute(sessions[0])   # 2024-01-02 21:00:00+00:00
  nyse.minute_to_session(pd.Timestamp("2024-01-02 15:00", tz="UTC"))

A few exchange_calendars names differ from the ``trading_calendars`` library used by older versions of Zipline. In particular, ``opens`` / ``session_open`` are the exchange's opening bell (9:30 for the NYSE); the first trading minute is ``first_minutes`` / ``session_first_minute``.


The ExchangeCalendar Class
~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``ExchangeCalendar`` class has many properties we should be thinking about if we were to build our own calendar for an exchange. These include properties such as:

  - Name of the Exchange
  - Timezone
  - Open Time
  - Close Time
  - Regular & Ad hoc Holidays
  - Special Opens & Closes
  - The days of the week on which the exchange trades

If you'd like to see all of the properties and methods available to you, please take a look at the `exchange_calendars documentation <https://github.com/gerrymanoim/exchange_calendars>`__ and the :ref:`Trading Calendar API <trading-calendar-api>` reference.

For example, the London Stock Exchange calendar in exchange_calendars (``XLON``) looks roughly like this:

.. code-block:: python

  class XLONExchangeCalendar(ExchangeCalendar):
      """
      Exchange calendar for the London Stock Exchange (XLON).

      Open Time: 8:00 AM, GMT
      Close Time: 4:30 PM, GMT
      """

      name = "XLON"
      tz = ZoneInfo("Europe/London")
      open_times = ((None, time(8)),)
      close_times = ((None, time(16, 30)),)

      @property
      def regular_holidays(self):
          return HolidayCalendar([
              LSENewYearsDay,
              GoodFriday,
              EasterMonday,
              MayBank,
              SpringBank,
              SummerBank,
              Christmas,
              WeekendChristmas,
              BoxingDay,
              WeekendBoxingDay,
          ])


You can create the ``Holiday`` objects mentioned in ``regular_holidays`` with :class:`pandas.tseries.holiday.Holiday`, for example:

.. code-block:: python

  from pandas.tseries.holiday import DateOffset, Holiday, MO

  SomeSpecialDay = Holiday(
      "Some Special Day",
      month=1,
      day=9,
      offset=DateOffset(weekday=MO(-1)),
  )


Building a Custom Trading Calendar
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Now we'll build our own custom trading calendar. This calendar will be used for trading assets that can be traded on a 24/7 exchange calendar. This means that it will be open on Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, and Sunday, and the exchange will open at 12AM and close at 11:59PM. The timezone which we'll use is UTC.

First we'll start off by importing some modules that will be useful to us.

.. code-block:: python

  # for setting our open and close times
  from datetime import time
  # for setting our timezone
  from zoneinfo import ZoneInfo

  # for creating and registering our calendar
  from zipline.utils.calendar_utils import (
      ExchangeCalendar,
      get_calendar,
      register_calendar_type,
  )


And now we'll actually build this calendar, which we'll call ``TFSExchangeCalendar``:

.. code-block:: python

  class TFSExchangeCalendar(ExchangeCalendar):
      """
      An exchange calendar for trading assets 24/7.

      Open Time: 12AM, UTC
      Close Time: 11:59PM, UTC
      """

      # The name of the exchange, which Zipline will look for when we run our
      # algorithm and pass TFS to the --trading-calendar CLI flag.
      name = "TFS"

      # The timezone in which we'll be running our algorithm.
      tz = ZoneInfo("UTC")

      # The times at which the exchange opens and closes each day. Each is a
      # sequence of (start date, time) pairs, so the time can change over the
      # calendar's history; None means "from the beginning".
      open_times = ((None, time(0, 0)),)
      close_times = ((None, time(23, 59)),)

      @property
      def weekmask(self):
          """The days on which our exchange will be open: every day."""
          return "1111111"


Finally, register the calendar class so that :func:`~zipline.utils.calendar_utils.get_calendar` can build it with Zipline's conventions. Put this in your ``~/.zipline/extension.py`` to make the calendar available to the ``zipline`` command line interface:

.. code-block:: python

  register_calendar_type("TFS", TFSExchangeCalendar)

  calendar = get_calendar("TFS")
  calendar.sessions_in_range("2024-01-05", "2024-01-08")  # includes the weekend

Registering a calendar *instance* with :func:`~zipline.utils.calendar_utils.register_calendar` also works, but then ``get_calendar`` returns that instance as it is, without applying Zipline's defaults, so construct it with ``side="right"`` yourself.

exchange_calendars already includes a 24/7 calendar, registered as ``"24/7"``, so in practice you could simply use ``get_calendar("24/7")``.


Conclusions
~~~~~~~~~~~

In order for you to run your algorithm with this calendar, you'll need have a data bundle in which your assets have dates that run through all days of the week. You can read about how to make your own data bundle in the :ref:`Writing a New Bundle <new_bundle>` documentation, or use the :ref:`csvdir bundle <csvdir_bundle>` for creating a bundle from CSV files.
