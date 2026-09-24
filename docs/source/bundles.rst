.. _data-bundles:

Data Bundles
------------

A data bundle is a collection of pricing data, adjustment data, and an asset
database. Bundles allow us to preload all of the data we will need to run
backtests and store the data for future runs.

.. _bundles-command:

Discovering Available Bundles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Zipline comes with a few bundles by default as well as the ability to register
new bundles. To see which bundles we have available, we may run the
``bundles`` command, for example:

.. code-block:: bash

   $ zipline bundles
   csvdir <no ingestions>
   massive 2026-09-22 21:35:19.809398
   my-custom-bundle 2026-09-20 20:35:19.809398
   my-custom-bundle 2026-09-20 20:34:53.654082
   my-custom-bundle 2026-09-20 20:34:48.401767
   quandl <no ingestions>

The output here shows that there are 4 bundles available:

- ``csvdir`` (provided by zipline, see :ref:`csvdir_bundle`)
- ``massive`` (provided by zipline, the default bundle, see
  :ref:`massive-data-bundle`)
- ``my-custom-bundle`` (added by the user)
- ``quandl`` (provided by zipline, see :ref:`quandl-data-bundle`)

The dates and times next to the name show the times when the data for this
bundle was ingested. We have run three different ingestions for
``my-custom-bundle``, and one for ``massive``. We have never ingested any data
for the ``csvdir`` and ``quandl`` bundles so they just show
``<no ingestions>`` instead.

.. _ingesting-data:

Ingesting Data
~~~~~~~~~~~~~~

The first step to using a data bundle is to ingest the data.
The ingestion process will invoke some custom bundle command and then write the data to a standard location that zipline can find.
By default the location where ingested data will be written is ``$ZIPLINE_ROOT/data/<bundle>`` where by default ``ZIPLINE_ROOT=~/.zipline``.
The ingestion step may take some time as it could involve downloading and processing a lot of data.
To ingest a bundle, run:

.. code-block:: bash

   $ zipline ingest [-b <bundle>]


where ``<bundle>`` is the name of the bundle to ingest, defaulting to ``massive``.

Old Data
~~~~~~~~

When the ``ingest`` command is used it will write the new data to a subdirectory
of ``$ZIPLINE_ROOT/data/<bundle>`` which is named with the current date. This
makes it possible to look at older data or even run backtests with the older
copies. Running a backtest with an old ingestion makes it easier to reproduce
backtest results later.

One drawback of saving all of the data by default is that the data directory
may grow quite large even if you do not want to use the data. As shown earlier,
we can list all of the ingestions with the :ref:`bundles command
<bundles-command>`. To solve the problem of leaking old data there is another
command: ``clean``, which will clear data bundles based on some time
constraints.

For example:

.. code-block:: bash

   # clean everything older than <date>
   $ zipline clean [-b <bundle>] --before <date>

   # clean everything newer than <date>
   $ zipline clean [-b <bundle>] --after <date>

   # keep everything in the range of [before, after] and delete the rest
   $ zipline clean [-b <bundle>] --before <date> --after <after>

   # clean all but the last <int> runs
   $ zipline clean [-b <bundle>] --keep-last <int>

Converting Data from Earlier Versions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Zipline 2.0 stores pricing data as Parquet. Bundles ingested by earlier
versions stored it with bcolz, and they still load, but the bcolz formats will
be removed in a future version. Since old data can't always be ingested again
(the Quandl WIKI dataset, for example, stopped updating in 2018), the
``convert`` command rewrites a bundle's bcolz data as Parquet in place. Reading
bcolz data needs the optional bcolz support, installed with
``pip install 'zipline[bcolz]'``:

.. code-block:: bash

   # convert every ingestion of <bundle>, keeping the bcolz data
   $ zipline convert [-b <bundle>]

   # convert, then delete the bcolz data
   $ zipline convert [-b <bundle>] --delete-bcolz

Ingestions that already use Parquet are left alone, so the command is safe to
run more than once.


Running Backtests with Data Bundles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Now that the data has been ingested we can use it to run backtests with the
``run`` command. The bundle to use can be specified with the ``--bundle`` option
like:

.. code-block:: bash

   $ zipline run --bundle <bundle> --algofile algo.py ...


We may also specify the date to use to look up the bundle data with the
``--bundle-timestamp`` option. Setting the ``--bundle-timestamp`` will cause
``run`` to use the most recent bundle ingestion that is less than or equal to
the ``bundle-timestamp``. This is how we can run backtests with older data.
``bundle-timestamp`` uses a less-than-or-equal-to relationship so that we can
specify the date that we ran an old backtest and get the same data that would
have been available to us on that date. The ``bundle-timestamp`` defaults to
the current day to use the most recent data.

Default Data Bundles
~~~~~~~~~~~~~~~~~~~~

.. _massive-data-bundle:

Massive Bundle
``````````````

The default ``massive`` bundle downloads US equities from `Massive
<https://massive.com>`_ (formerly Polygon.io): unadjusted daily bars for the
whole market, splits, cash dividends, and asset metadata for common stocks,
ADRs and ETFs, including delisted ones. Downloading needs an API key, which is
free: create an account at https://massive.com and pass the key in the
``MASSIVE_API_KEY`` environment variable:

.. code-block:: bash

   $ MASSIVE_API_KEY=<your key> zipline ingest

Massive's free plan allows 5 requests a minute and two years of history. The
bundle downloads one session of bars per request, and the listed tickers as of
the first of each month in about a dozen requests each, so the first ingestion
takes about three hours. Downloaded bars and listings are kept in
``$ZIPLINE_ROOT/cache/massive``, and later ingestions only download what they
are missing, the delisted tickers, splits and dividends, which takes about five
minutes. A session's bars are downloaded once they are final, four hours after
the session's close.

Paid plans allow unlimited requests and more history. Tell the bundle about
them with environment variables:

``MASSIVE_CALLS_PER_MINUTE``
   The plan's rate limit, 5 by default. 0 means unlimited.

``MASSIVE_START_DATE``
   The first session to ingest, two years ago by default.

To ingest other security types, or to set these in code, register a bundle
with :func:`zipline.data.bundles.massive.massive_equities` in your
``extension.py``:

.. code-block:: python

   import pandas as pd

   from zipline.data.bundles import register
   from zipline.data.bundles.massive import massive_equities

   register(
       "massive-long",
       massive_equities(
           start_session=pd.Timestamp("2006-01-03"),
           calls_per_minute=0,
           types=("CS", "ADRC", "ETF", "PFD"),
       ),
       calendar_name="XNYS",
   )

Each bar's ticker is matched to the security it meant that day, from the
listings as of the first of each month and of the first and last sessions, and
the delisting dates. A company that changes its ticker (e.g. FB to META) keeps
one sid, found by either ticker on the dates it used it, and a ticker reused by
another company gets a new sid. Massive's identifiers change now and then, so
listings are linked into securities by composite FIGI, and consecutive
listings of a ticker by name, issuer (CIK) or FIGI. A security that is
reorganized under a new issuer and name, e.g. into a new holding company,
gets a new sid. A security keeps its sid in every ingestion. Assets list their
primary exchange (``XNYS``, ``XNAS``, ``ARCX``, ...) and trade on that
exchange's calendar.

.. note::

   Massive's terms allow individual use only. The bundle stores what it
   downloads on your machine; don't share it.

.. _alpaca-data-bundle:

Alpaca Bundle
`````````````

The ``alpaca`` bundle downloads US equities from `Alpaca
<https://alpaca.markets>`_'s market data API: unadjusted daily bars from the
consolidated (SIP) feed since 2016, splits, cash dividends, and asset metadata
for the stocks and ETFs listed on US exchanges, including those that were since
acquired or removed. It needs the API keys of a free paper trading account:

.. code-block:: bash

   $ APCA_API_KEY_ID=<key id> APCA_API_SECRET_KEY=<secret key> zipline ingest -b alpaca

Alpaca's free plan allows 200 requests a minute. The first ingestion
downloads the bars from 2016 and the corporate actions since, which takes
about an hour; they are kept in ``$ZIPLINE_ROOT/cache/alpaca``, and later
ingestions only download new sessions and the last three months of corporate
actions, which Alpaca sometimes reports late. ``ALPACA_START_DATE`` sets the
first session to ingest, and ``ALPACA_CALLS_PER_MINUTE`` the rate limit of a
paid plan (0 means unlimited); :func:`zipline.data.bundles.alpaca.alpaca_equities`
sets these in code.

Each bar is labelled with the ticker it traded under that day. A ticker is
followed through its later renames to the merger or removal that ended the
security, or else to the asset listed under it today. So a renamed company
(e.g. FB to META) keeps one sid, found by either ticker on the dates it used
it, a ticker reused by another company gets a new sid, and a one-for-one
merger into a new holding company that keeps the ticker continues the
security. Securities keep their sids in every ingestion. Stocks that left
their exchange without a merger or removal, e.g. for trading over the
counter, are left out.

.. note::

   Alpaca's terms don't allow redistributing its data. The bundle stores what
   it downloads on your machine; don't share it.

.. _quandl-data-bundle:

Quandl WIKI Bundle
``````````````````

The ``quandl`` data bundle uses Quandl's WIKI Prices dataset, now hosted by
`Nasdaq Data Link <https://data.nasdaq.com>`_.
The quandl data bundle includes daily pricing data, splits, cash dividends, and
asset metadata for US equities.

The dataset is free, but downloading it requires an API key: create a free
account at https://data.nasdaq.com and pass the key in the ``QUANDL_API_KEY``
environment variable. To ingest the ``quandl`` data bundle, run:

.. code-block:: bash

   $ QUANDL_API_KEY=<your key> zipline ingest -b quandl

The ingestion downloads a file of roughly 450MB and processes it, so it takes
a few minutes. ``QUANDL_DOWNLOAD_ATTEMPTS`` sets how many times to retry the
download (default 5).

.. note::

   The WIKI dataset stopped updating in March 2018. It is still reasonable for
   trying out Zipline without setting up your own dataset.

.. note::

   Older versions of Zipline also provided a ``quantopian-quandl`` bundle,
   which downloaded a pre-built copy of this data from a Quantopian-hosted
   mirror. That mirror no longer exists, so the bundle has been removed.

.. _new_bundle:

Writing a New Bundle
~~~~~~~~~~~~~~~~~~~~

Data bundles exist to make it easy to use different data sources with
zipline. To add a new bundle, one must implement an ``ingest`` function.

The ``ingest`` function is responsible for loading the data into memory and
passing it to a set of writer objects provided by zipline to convert the data to
zipline's internal format. The ingest function may work by downloading data from
a remote location like the ``quandl`` bundle or it may just
load files that are already on the machine. The function is provided with
writers that will write the data to the correct location transactionally. If an
ingestion fails part way through the bundle will not be written in an incomplete
state.

The signature of the ingest function should be:

.. code-block:: python

   ingest(environ,
          asset_db_writer,
          minute_bar_writer,
          daily_bar_writer,
          adjustment_writer,
          calendar,
          start_session,
          end_session,
          cache,
          show_progress,
          output_dir)

``environ``
```````````

``environ`` is a mapping representing the environment variables to use. This is
where any custom arguments needed for the ingestion should be passed, for
example: the ``quandl`` bundle uses the environment to pass the API key and the
download retry attempt count.

``asset_db_writer``
```````````````````

``asset_db_writer`` is an instance of :class:`~zipline.assets.AssetDBWriter`.
This is the writer for the asset metadata which provides the asset lifetimes and
the symbol to asset id (sid) mapping. This may also contain the asset name,
exchange and a few other columns. To write data, invoke
:meth:`~zipline.assets.AssetDBWriter.write` with dataframes for the various
pieces of metadata. More information about the format of the data exists in the
docs for write.

``minute_bar_writer``
`````````````````````

``minute_bar_writer`` is an instance of
:class:`~zipline.data.parquet_minute_bars.ParquetMinuteBarWriter`. This writer
stores minute bars as a Parquet dataset, later read by a
:class:`~zipline.data.parquet_minute_bars.ParquetMinuteBarReader`. If minute
data is provided, users should call
:meth:`~zipline.data.parquet_minute_bars.ParquetMinuteBarWriter.write` once with
an iterable of (sid, dataframe) tuples, where each dataframe is indexed by
minute and has ``open``, ``high``, ``low``, ``close`` and ``volume`` columns.
Minutes must be trading minutes of the bundle's calendar; naive minutes are
taken as UTC. Only minutes with a bar need to be provided. The
``show_progress`` argument should also be forwarded to this method. If the data
source does not provide minute level data, then there is no need to call the
write method.

.. note::

   The data passed to
   :meth:`~zipline.data.parquet_minute_bars.ParquetMinuteBarWriter.write` may be
   a lazy iterator or generator to avoid loading all of the minute data into
   memory at a single time. Writing sids in ascending order makes reads faster.

``daily_bar_writer``
````````````````````

``daily_bar_writer`` is an instance of
:class:`~zipline.data.parquet_daily_bars.ParquetDailyBarWriter`. This writer
stores daily bars as a Parquet dataset, later read by a
:class:`~zipline.data.parquet_daily_bars.ParquetDailyBarReader`. If daily data is
provided, users should call
:meth:`~zipline.data.parquet_daily_bars.ParquetDailyBarWriter.write` once with an
iterable of (sid, dataframe) tuples, where each dataframe is indexed by session
and has ``open``, ``high``, ``low``, ``close`` and ``volume`` columns. Sessions
missing between an asset's first and last bar are stored as missing data. The
``show_progress`` argument should also be forwarded to this method. If the data
source does not provide daily data, then there is no need to call the write
method. If no daily data is provided but minute data is provided, a daily rollup
will happen to service daily history requests.

.. note::

   Like the ``minute_bar_writer``, the data passed to
   :meth:`~zipline.data.parquet_daily_bars.ParquetDailyBarWriter.write` may be a
   lazy iterable or generator to avoid loading all of the data into memory at
   once. For both writers, a sid may only appear once in the data iterable, and
   the method may only be called once.

``adjustment_writer``
`````````````````````

``adjustment_writer`` is an instance of
:class:`~zipline.data.adjustments.SQLiteAdjustmentWriter`. This writer is
used to store splits, mergers, dividends, and stock dividends. The data should
be provided as dataframes and passed to
:meth:`~zipline.data.adjustments.SQLiteAdjustmentWriter.write`. Each of
these fields are optional, but the writer can accept as much of the data as you
have.

``calendar``
````````````

``calendar`` is an instance of
:class:`zipline.utils.calendar_utils.ExchangeCalendar` (see :doc:`trading-calendars`).
The calendar is provided to help some bundles generate queries for the days
needed.

``start_session``
`````````````````

``start_session`` is a tz-naive :class:`pandas.Timestamp` session label
indicating the first day that the bundle should load data for.

``end_session``
```````````````

``end_session`` is a tz-naive :class:`pandas.Timestamp` session label
indicating the last day that the bundle should load data for.

``cache``
`````````

``cache`` is an instance of :class:`~zipline.utils.cache.dataframe_cache`. This
object is a mapping from strings to dataframes. This object is provided in case
an ingestion crashes part way through. The idea is that the ingest function
should check the cache for raw data, if it doesn't exist in the cache, it should
acquire it and then store it in the cache. Then it can parse and write the
data. The cache will be cleared only after a successful load, this prevents the
ingest function from needing to re-download all the data if there is some bug in
the parsing. If it is very fast to get the data, for example if it is coming
from another local file, then there is no need to use this cache.

``show_progress``
`````````````````

``show_progress`` is a boolean indicating that the user would like to receive
feedback about the ingest function's progress fetching and writing the
data. Some examples for where to show how many files you have downloaded out of
the total needed, or how far into some data conversion the ingest function
is. One tool that may help with implementing ``show_progress`` for a loop is
:class:`~zipline.utils.cli.maybe_show_progress`. This argument should always be
forwarded to ``minute_bar_writer.write`` and ``daily_bar_writer.write``.


``output_dir``
``````````````

``output_dir`` is a string representing the file path where all the data will be
written. ``output_dir`` will be some subdirectory of ``$ZIPLINE_ROOT`` and will
contain the time of the start of the current ingestion. This can be used to
directly move resources here if for some reason your ingest function can produce
its own outputs without the writers, for example by extracting a pre-built
bundle archive into ``output_dir``. Register such a bundle with
``create_writers=False``.

.. _csvdir_bundle:

Ingesting Data from .csv Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Zipline provides a bundle called ``csvdir``, which allows users to ingest data
from ``.csv`` files. Put one file per asset, named ``<SYMBOL>.csv``, in a
``daily`` or ``minute`` subdirectory of your data directory, or in both. The
format of the files should be in OHLCV format, with dates, dividends, and
splits. A sample is provided below. There are other samples for testing
purposes in ``tests/resources/csvdir_samples``. Minute files are indexed by UTC
timestamps.

Each symbol gets one sid, whether it has daily files, minute files or both, and
its lifetime covers all of its bars. Splits and dividends are read from the
daily files, or from the minute files if there are no daily ones.

.. code-block:: text

	 date,open,high,low,close,volume,dividend,split
	 2012-01-03,58.485714,58.92857,58.42857,58.747143,75555200,0.0,1.0
	 2012-01-04,58.57143,59.240002,58.468571,59.062859,65005500,0.0,1.0
	 2012-01-05,59.278572,59.792858,58.952858,59.718571,67817400,0.0,1.0
	 2012-01-06,59.967144,60.392857,59.888573,60.342857,79573200,0.0,1.0
	 2012-01-09,60.785713,61.107143,60.192856,60.247143,98506100,0.0,1.0
	 2012-01-10,60.844284,60.857143,60.214287,60.462856,64549100,0.0,1.0
	 2012-01-11,60.382858,60.407143,59.901428,60.364285,53771200,0.0,1.0

Once you have your data in the correct format, you can edit your ``extension.py`` file in
``~/.zipline/extension.py`` and import the csvdir bundle, along with ``pandas``.

.. code-block:: python

	 import pandas as pd

	 from zipline.data.bundles import register
	 from zipline.data.bundles.csvdir import csvdir_equities

We'll then want to specify the start and end sessions of our bundle data:

.. code-block:: python

	 start_session = pd.Timestamp('2016-1-1')
	 end_session = pd.Timestamp('2018-1-1')

And then we can ``register()`` our bundle, and pass the location of the directory in which
our ``.csv`` files exist:

.. code-block:: python

    register(
        'custom-csvdir-bundle',
        csvdir_equities(
            ['daily'],
            '/path/to/your/csvs',
        ),
        calendar_name='NYSE', # US equities
        start_session=start_session,
        end_session=end_session
    )

To finally ingest our data, we can run:

.. code-block:: bash

	 $ zipline ingest -b custom-csvdir-bundle
	 [2018-01-03 04:30:51,843] INFO: zipline.data.bundles.core: Ingesting custom-csvdir-bundle.
	 [2018-01-03 04:30:51,845] INFO: zipline.data.bundles.csvdir: FAKE: sid 0
	 [2018-01-03 04:30:51,849] INFO: zipline.data.bundles.csvdir: FAKE1: sid 1
	 [2018-01-03 04:30:51,851] INFO: zipline.data.bundles.csvdir: FAKE2: sid 2

	 # optionally, we can pass the location of our csvs via the command line
	 $ CSVDIR=/path/to/your/csvs zipline ingest -b custom-csvdir-bundle


If you would like to use equities that are not in the NYSE calendar, or the existing zipline calendars,
you can look at :doc:`trading-calendars` to build a custom trading calendar that you can then pass
the name of to ``register()``.
