# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This is a revival of Quantopian's abandoned Zipline (event-driven backtesting library). Work stalled in Dec 2020 on Python 3.6 / pandas 0.22 and resumed in 2026 on the `modernize` branch. Targets are Python 3.12+, pandas 3+, numpy 2+, Cython 3, uv, ruff and ty. Dead dependencies are being replaced: `trading_calendars` becomes `exchange_calendars`, `empyrical` becomes `empyrical-reloaded` (same import name), `bcolz` becomes `bcolz-zipline` (same import name) and `nose` becomes `pytest`. `six`, `python-interface` and `distutils` are being removed. Much of the code still reflects the old stack, so expect breakage. Commits use pandas-style prefixes (`CLN:`, `DEPS:`, `BLD:`, `CI:`, `DOC:`, `TST:`).

## Setup and commands

Everything goes through uv. The Python version comes from `.python-version` (3.12, the minimum supported):

```bash
uv sync                      # create .venv, install deps + dev group, build the Cython extensions
uv run python -c "import zipline"
```

The project is installed in editable mode, but the Cython extensions are compiled at install time. `[tool.uv].cache-keys` in `pyproject.toml` makes `uv sync`/`uv run` rebuild them automatically when any `.pyx`/`.pxd`/`.pxi` changes. New extensions must be added to `ext_modules` in `setup.py`, which only holds the extension list. All metadata is in `pyproject.toml`, and the version comes from git tags via setuptools-scm, which writes the ignored `zipline/_version.py`.

The optional `talib` extra needs the TA-Lib C library installed on the system first (`brew install ta-lib`).

Lint, types and tests:

```bash
uv run ruff check zipline tests
uv run ruff format zipline tests
uv run ty check zipline
uv run pytest                                   # full suite
uv run pytest tests/test_algorithm.py
uv run pytest "tests/test_algorithm.py::TestMiscellaneousAPI::test_zipline_api_resolves_dynamically"
uv run pytest -n auto                            # parallel (pytest-xdist)
```

Test cases are still `unittest`-style classes, some parameterized with `parameterized`. pytest collects them directly.

## Architecture

**Simulation loop.** `TradingAlgorithm` (`zipline/algorithm.py`) holds user callbacks (`initialize`, `handle_data`, `before_trading_start`, scheduled functions), the blotter, metrics tracker, and pipeline engine. `run()` builds an `AlgorithmSimulator` (`zipline/gens/tradesimulation.py`), which iterates the Cython `MinuteSimulationClock` (`zipline/gens/sim_engine.pyx`) emitting bar/session/before-trading events. On each bar it processes open orders through the blotter (`zipline/finance/blotter/`) using slippage/commission models, updates the `Ledger`, then calls user code with a `BarData` (`zipline/_protocol.pyx`). Performance output is produced by `MetricsTracker` (`zipline/finance/metrics/`), which aggregates pluggable metric sets registered by name (`default`, `classic`, `none`).

**User-facing API.** `zipline.api` only statically contains a few imports; functions like `order`, `symbol`, `record`, `schedule_function` are methods on `TradingAlgorithm` decorated with `@api_method` (`zipline/utils/api_support.py`), which injects them into the `zipline.api` module and dispatches to the currently running algorithm via a context-local stack. `zipline/api.pyi` is a type stub for this dynamic namespace; update it when adding or changing API methods.

**Data layer.** `DataPortal` (`zipline/data/data_portal.py`) is the single facade the algorithm uses for current prices and `history()`. It composes bar readers (bcolz daily/minute, HDF5 daily, in-memory), `SQLiteAdjustmentReader` for splits/dividends/mergers, history loaders with adjustment caching, continuous-future readers, and an `AssetFinder` (`zipline/assets/`, SQLite-backed via SQLAlchemy with alembic-style migrations in `asset_db_migrations.py`). Hot paths (asset objects, adjusted window iteration, minute-bar indexing, resampling) are in Cython.

**Bundles.** `zipline ingest` writes data into `$ZIPLINE_ROOT` (default `~/.zipline`) via bundles registered with `zipline.data.bundles.register` (`quandl`, `csvdir`, etc.). A bundle's ingest function receives asset/daily/minute/adjustment writers; `load()` returns the matching readers. The CLI (`zipline/__main__.py`, click) wires `run`/`ingest`/`clean`/`bundles` to `zipline/utils/run_algo.py`, which also backs the programmatic `run_algorithm()`.

**Pipeline.** `zipline/pipeline/` is a lazy, vectorized cross-sectional computation framework. `Term`s (`Factor`, `Filter`, `Classifier`, `BoundColumn` of a `DataSet`) form a dependency graph (`graph.py`, networkx) that `SimplePipelineEngine` (`engine.py`) topologically executes over a date range, using per-dataset `PipelineLoader`s (`loaders/`) that return `AdjustedArray`s (`zipline/lib/adjusted_array.py` + Cython window specializations). `Domain`s (`domain.py`) tie datasets to a calendar/country. The algorithm runs pipelines in chunks ahead of time and serves results via `pipeline_output()`.

**Extension points.** `zipline/extensions.py` provides the `Registry`/`register` mechanism used for pluggable blotters, metrics sets, calendars, etc., and loads user extension files (`-x`/`extension.py` in `$ZIPLINE_ROOT`).

## Testing conventions

- Test classes subclass `ZiplineTestCase` plus fixture mixins from `zipline/testing/fixtures.py` (e.g. `WithDataPortal`, `WithMakeAlgo`, `WithAssetFinder`, `WithSeededRandomPipelineEngine`). Mixins are configured with class attributes (`START_DATE`, `ASSET_FINDER_EQUITY_SIDS`, `make_equity_info`, ...) that you override.
- **Do not override `setUp`/`setUpClass`/`tearDown`** (they are `@final`). Implement `init_class_fixtures` / `init_instance_fixtures`, always call `super()`, and register cleanup via `enter_class_context`/`enter_instance_context` or `add_*_callback`.
- `tests/test_examples.py` compares `zipline/examples/*` output against expected results stored in `tests/resources/example_data.tar.gz`; regenerate with `tests/resources/rebuild_example_data` when example behavior intentionally changes.
