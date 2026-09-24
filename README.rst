.. image:: https://media.quantopian.com/logos/open_source/zipline-logo-03_.png
    :target: https://github.com/fangchenli/zipline
    :width: 212px
    :align: center
    :alt: Zipline

=============

|ci status|

Zipline is a Pythonic, event-driven backtesting library for algorithmic
trading. It was built and open-sourced by Quantopian, which powered its
hosted research platform with it until the company shut down in 2020.

This repository is a revival of Zipline on a modern stack: Python 3.12+,
pandas 3, numpy 2, Cython 3, SQLAlchemy 2 and
`exchange_calendars <https://github.com/gerrymanoim/exchange_calendars>`_.

Features
========

- **Ease of use:** Zipline tries to get out of your way so that you can focus
  on algorithm development. See below for a code example.
- **"Batteries included":** common statistics like moving averages and
  linear regression can be readily accessed from within an algorithm, and
  the Pipeline API computes cross-sectional factors efficiently.
- **PyData integration:** historical data comes in and performance results
  go out as pandas DataFrames, so they fit into the rest of the PyData
  ecosystem.
- **Statistics and machine learning libraries:** use matplotlib, scipy,
  statsmodels and scikit-learn to develop, analyze and visualize trading
  systems.

Installation
============

Zipline requires Python 3.12 or newer. It isn't published to PyPI from this
repository yet, so install it from source:

.. code:: bash

    $ pip install git+https://github.com/fangchenli/zipline

This builds Zipline's Cython extensions, so it needs a C compiler. Optional
TA-Lib support is available with the ``talib`` extra.

Quickstart
==========

The following code implements a simple dual moving average algorithm.

.. code:: python

    from zipline.api import order_target, record, symbol

    def initialize(context):
        context.i = 0
        context.asset = symbol('AAPL')


    def handle_data(context, data):
        # Skip first 300 days to get full windows
        context.i += 1
        if context.i < 300:
            return

        # Compute averages
        # data.history() has to be called with the same params
        # from above and returns a pandas dataframe.
        short_mavg = data.history(context.asset, 'price', bar_count=100, frequency="1d").mean()
        long_mavg = data.history(context.asset, 'price', bar_count=300, frequency="1d").mean()

        # Trading logic
        if short_mavg > long_mavg:
            # order_target orders as many shares as needed to
            # achieve the desired number of shares.
            order_target(context.asset, 100)
        elif short_mavg < long_mavg:
            order_target(context.asset, 0)

        # Save values for later inspection
        record(AAPL=data.current(context.asset, 'price'),
               short_mavg=short_mavg,
               long_mavg=long_mavg)

Backtests run against a *data bundle*. The default ``quandl`` bundle
downloads Quandl's free WIKI Prices dataset (now hosted by Nasdaq Data Link),
which covers US equities until March 2018. Create a free account at
https://data.nasdaq.com to get an API key, then ingest the data and run the
algorithm:

.. code:: bash

    $ QUANDL_API_KEY=<your key> zipline ingest
    $ zipline run -f dual_moving_average.py --start 2014-1-1 --end 2018-1-1 -o dma.parquet --no-benchmark

The resulting performance DataFrame is saved in ``dma.parquet``, which you
can load with ``zipline.utils.results.read_results`` and analyze from within
Python. To backtest on your own data, write
daily OHLCV CSV files and ingest them with the ``csvdir`` bundle; see the
data bundles documentation in ``docs/source/bundles.rst``.

You can find other examples in the ``zipline/examples`` directory.

Development
===========

The project uses `uv <https://docs.astral.sh/uv/>`_:

.. code:: bash

    $ uv sync                 # create .venv, install dependencies, build extensions
    $ uv run pytest -n auto   # run the test suite
    $ uv run ruff check zipline tests scripts
    $ uv run ruff format zipline tests scripts
    $ uv run ty check zipline

See ``docs/source/development-guidelines.rst`` for more.

Upgrading from Zipline 1.x
==========================

- Session labels (for example ``SimulationParameters`` start and end
  sessions, asset start and end dates, and pipeline dates) are tz-naive dates.
  Minutes and other points in time are tz-aware UTC timestamps.
- Trading calendars come from ``exchange_calendars``. Use
  ``zipline.utils.calendar_utils.get_calendar``, which applies Zipline's
  conventions.
- ``data.history()`` with several assets *and* several fields returns a
  DataFrame with ``(field, asset)`` MultiIndex columns instead of the removed
  ``pd.Panel``, so ``history(...)['price']`` still gives a dates x assets
  frame.
- The ``quantopian-quandl`` bundle is gone (its download mirror no longer
  exists), and ``quandl`` is the default bundle.

.. |ci status| image:: https://github.com/fangchenli/zipline/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/fangchenli/zipline/actions/workflows/ci.yml
