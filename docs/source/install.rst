Install
=======

Zipline requires Python 3.12 or newer, and is tested on Linux and macOS with
Python 3.12, 3.13 and 3.14.

Zipline isn't published to PyPI from this repository yet, so it is installed
from source. Its Cython extensions are compiled during installation, so you
need a C compiler:

- **Linux:** your distribution's compiler toolchain, e.g. ``build-essential``
  on Debian and Ubuntu, or ``gcc`` on Fedora.
- **macOS:** the Xcode command line tools (``xcode-select --install``).

Every other dependency, including numpy, pandas and pyarrow, is installed
from binary wheels.

Bundles ingested by Zipline before 2.0 store their pricing data with bcolz.
Reading or converting them (see :doc:`bundles`) needs the optional bcolz
support, installed with the ``bcolz`` extra, e.g.
``pip install 'zipline[bcolz] @ git+https://github.com/fangchenli/zipline'``.

We recommend installing Zipline into a virtual environment rather than your
system Python.

Installing with ``pip``
-----------------------

.. code-block:: bash

   $ python -m venv .venv
   $ source .venv/bin/activate
   $ pip install git+https://github.com/fangchenli/zipline

Installing with ``uv``
----------------------

`uv <https://docs.astral.sh/uv/>`_ manages the virtual environment for you:

.. code-block:: bash

   $ uv venv
   $ uv pip install git+https://github.com/fangchenli/zipline

or, in a uv-managed project:

.. code-block:: bash

   $ uv add git+https://github.com/fangchenli/zipline

Optional dependencies
---------------------

`TA-Lib <https://ta-lib.org>`_ support (used by some examples) is available
through the ``talib`` extra. The ``ta-lib`` package ships binary wheels that
include the TA-Lib C library, so nothing else needs to be installed:

.. code-block:: bash

   $ pip install "zipline[talib] @ git+https://github.com/fangchenli/zipline"

The pipeline progress bar in Jupyter notebooks uses ``ipywidgets`` if it is
installed.

Installing for development
--------------------------

See :doc:`development-guidelines`.
