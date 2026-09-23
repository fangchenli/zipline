Development Guidelines
======================
This page is intended for developers of Zipline, people who want to contribute to the Zipline codebase or documentation, or people who want to install from source and make local changes to their copy of Zipline.

All contributions, bug reports, bug fixes, documentation improvements, enhancements and ideas are welcome. We `track issues`__ on GitHub.

__ https://github.com/fangchenli/zipline/issues

Creating a Development Environment
----------------------------------

Zipline uses `uv`__ to manage its development environment. First, clone Zipline and create a branch for your changes:

__ https://docs.astral.sh/uv/

.. code-block:: bash

   $ git clone git@github.com:your-github-username/zipline.git
   $ cd zipline
   $ git checkout -b some-short-descriptive-name

You'll need a C compiler to build Zipline's Cython extensions (see :doc:`install`). Then run:

.. code-block:: bash

   $ uv sync

This creates a virtual environment in ``.venv`` using the Python version in ``.python-version`` (3.12, the oldest supported version), installs Zipline in editable mode with its development dependencies, and compiles the Cython extensions. ``uv sync`` and ``uv run`` rebuild the extensions automatically whenever a ``.pyx``, ``.pxd`` or ``.pxi`` file changes. New extensions must be added to ``ext_modules`` in ``setup.py``; all other project metadata lives in ``pyproject.toml``.

After installation, you should be able to use the ``zipline`` command line interface:

.. code-block:: bash

   $ uv run zipline --help


Style Guide & Running Tests
---------------------------

Before submitting patches or pull requests, please make sure these pass. Our `continuous integration`__ runs the same commands on Linux and macOS with Python 3.12, 3.13 and 3.14:

__ https://github.com/fangchenli/zipline/actions

.. code-block:: bash

   $ uv run ruff check zipline tests scripts
   $ uv run ruff format zipline tests scripts
   $ uv run ty check zipline
   $ uv run pytest -n auto
   $ uv run pytest --doctest-modules zipline

`ruff`__ handles linting and formatting, and `ty`__ checks types. Tests use `pytest`__, and the ``-n auto`` option runs them in parallel with pytest-xdist. You can run a single file, class or test in the usual pytest way:

__ https://docs.astral.sh/ruff/
__ https://docs.astral.sh/ty/
__ https://docs.pytest.org/

.. code-block:: bash

   $ uv run pytest tests/test_algorithm.py
   $ uv run pytest "tests/test_algorithm.py::TestMiscellaneousAPI::test_zipline_api_resolves_dynamically"

Most tests are ``unittest``-style classes that subclass ``ZiplineTestCase`` together with fixture mixins from ``zipline/testing/fixtures.py``. Don't override ``setUp``/``setUpClass``; implement ``init_class_fixtures`` / ``init_instance_fixtures`` instead, and register cleanup with ``enter_class_context`` / ``enter_instance_context``. Test classes whose names start with an underscore are abstract bases and aren't collected.


Type stubs
----------

Type checkers can't see inside the compiled Cython extensions, so each one has a ``.pyi`` stub file next to its ``.pyx`` source. Update the stub when you change an extension's Python-visible API.

``zipline.api`` is mostly populated at runtime by the ``@api_method`` decorator on ``TradingAlgorithm``, so it is described by a generated stub, ``zipline/api.pyi``. Regenerate it after adding or changing an API method:

.. code-block:: bash

   $ uv run python scripts/gen_api_stub.py
   $ uv run ruff check --fix zipline/api.pyi && uv run ruff format zipline/api.pyi


Benchmarks
----------

Performance is measured with `airspeed velocity`__ (asv). The benchmarks in ``benchmarks/`` cover the bar readers, ``DataPortal`` history and current-price lookups, a Pipeline run, end-to-end daily and minute backtests, and writing bars, measuring both time and peak memory. They run against a synthetic bundle that ``benchmarks/data.py`` generates. Benchmarks that touch storage are parameterized by backend, so a new storage format is compared with the existing ones by adding it to ``BACKENDS`` in that module.

__ https://asv.readthedocs.io

.. code-block:: bash

   # Check that every benchmark runs (one sample each), using the current environment:
   $ uv run --group bench asv run --python=same --quick

   # Compare your branch with master. asv builds each commit in its own uv
   # environment, runs the suite and reports significant changes:
   $ uv run --group bench asv continuous master HEAD

   # Restrict to some benchmarks with a regular expression:
   $ uv run --group bench asv continuous master HEAD --bench DailyBarReader

The first time, ``asv machine`` asks a few questions about the machine (``asv machine --yes`` accepts the defaults). CI only checks that the benchmarks run, since shared runners are too noisy for timings, so do performance comparisons locally, on a quiet machine.


Updating dependencies
---------------------

Dependencies are declared in ``pyproject.toml`` and pinned for development and CI in ``uv.lock``. After changing a dependency, run ``uv lock`` and commit the updated lockfile; CI installs with ``uv sync --locked``.


Contributing to the Docs
------------------------

The documentation lives in ``docs/source/``, where each `reStructuredText`__ (``.rst``) file is a separate section. To add a section, create a new file called ``some-descriptive-name.rst`` and add ``some-descriptive-name`` to ``appendix.rst``. To edit a section, simply open up one of the existing files, make your changes, and save them.

__ https://en.wikipedia.org/wiki/ReStructuredText

We use `Sphinx`__ to generate the documentation. To build it locally, run:

__ https://www.sphinx-doc.org/en/master/

.. code-block:: bash

   $ uv run --group docs sphinx-build -b html docs/source docs/build/html
   $ {BROWSER} docs/build/html/index.html


Commit messages
---------------

Standard prefixes to start a commit message:

.. code-block:: text

   BLD: change related to building Zipline
   BUG: bug fix
   CI: continuous integration
   CLN: code cleanup
   DEP: deprecate something, or remove a deprecated object
   DEPS: dependency changes
   DEV: development tool or utility
   DOC: documentation
   ENH: enhancement
   MAINT: maintenance commit (refactoring, porting, etc)
   REV: revert an earlier commit
   STY: style fix (formatting, lint)
   TST: addition or modification of tests
   TYP: type annotations and stubs
   REL: related to releasing Zipline
   PERF: performance enhancements


Some commit style guidelines:

Commit lines should be no longer than `72 characters`__. The first line of the commit should include one of the above prefixes. There should be an empty line between the commit subject and the body of the commit. In general, the message should be in the imperative tense. Best practice is to include not only what the change is, but why the change was made.

__ https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project

**Example:**

.. code-block:: text

   MAINT: Remove unused calculations of max_leverage, et al.

   In the performance period the max_leverage, max_capital_used,
   cumulative_capital_used were calculated but not used.

   At least one of those calculations, max_leverage, was causing a
   divide by zero error.

   Instead of papering over that error, the entire calculation was
   a bit suspect so removing, with possibility of adding it back in
   later with handling the case (or raising appropriate errors) when
   the algorithm has little cash on hand.


Formatting Docstrings
---------------------

When adding or editing docstrings for classes, functions, etc, we use the `numpydoc style`__ as the canonical reference.

__ https://numpydoc.readthedocs.io/en/latest/format.html


Updating the Whatsnew
---------------------

We have a set of ``whatsnew`` files in ``docs/source/whatsnew`` that are used for documenting changes that have occurred between different versions of Zipline.
Once you've made a change to Zipline, in your Pull Request, please update the most recent ``whatsnew`` file with a comment about what you changed. You can find examples in previous ``whatsnew`` files.
