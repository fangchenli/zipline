#
# Copyright 2015 Quantopian, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os

from exchange_calendars.calendar_utils import global_calendar_dispatcher

# This is *not* a place to dump arbitrary classes/modules for convenience,
# it is a place to expose the public interfaces.
from zipline import extensions as ext
from zipline.finance.blotter import Blotter

from . import api, data, finance, gens, utils
from ._version import __version__
from .algorithm import TradingAlgorithm
from .utils.calendar_utils import get_calendar
from .utils.run_algo import run_algorithm

# PERF: Fire a warning if calendars were instantiated during zipline import.
# Having calendars doesn't break anything per-se, but it makes zipline imports
# noticeably slower, which becomes particularly noticeable in the Zipline CLI.
if global_calendar_dispatcher._calendars:
    import warnings

    warnings.warn(
        "Found ExchangeCalendar instances after zipline import.\n"
        "Zipline startup will be much slower until this is fixed!",
        stacklevel=2,
    )
    del warnings
del global_calendar_dispatcher


extension_args = ext.Namespace()


def load_ipython_extension(ipython):
    from .__main__ import zipline_magic

    ipython.register_magic_function(zipline_magic, "line_cell", "zipline")


if os.name == "nt":
    # we need to be able to write to our temp directoy on windows so we
    # create a subdir in %TMP% that has write access and use that as %TMP%
    def _():
        import atexit
        import tempfile

        tempfile.tempdir = tempdir = tempfile.mkdtemp()

        @atexit.register
        def cleanup_tempdir():
            import shutil

            shutil.rmtree(tempdir)

    _()
    del _

__all__ = [
    "Blotter",
    "TradingAlgorithm",
    "api",
    "data",
    "finance",
    "get_calendar",
    "gens",
    "run_algorithm",
    "utils",
    "extension_args",
    "__version__",
]


del os
