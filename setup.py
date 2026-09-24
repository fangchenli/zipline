#
# Copyright 2014 Quantopian, Inc.
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
"""Build configuration for zipline's Cython extensions.

All project metadata lives in pyproject.toml.
"""

import numpy
from Cython.Build import cythonize
from setuptools import Extension, setup


def window_specialization(typename):
    """Make an extension for an AdjustedArrayWindow specialization."""
    return Extension(
        f"zipline.lib._{typename}window",
        [f"zipline/lib/_{typename}window.pyx"],
        depends=["zipline/lib/_windowtemplate.pxi"],
    )


ext_modules = [
    Extension("zipline.assets._assets", ["zipline/assets/_assets.pyx"]),
    Extension(
        "zipline.assets.continuous_futures",
        ["zipline/assets/continuous_futures.pyx"],
    ),
    Extension("zipline.lib.adjustment", ["zipline/lib/adjustment.pyx"]),
    Extension("zipline.lib._factorize", ["zipline/lib/_factorize.pyx"]),
    window_specialization("float64"),
    window_specialization("int64"),
    window_specialization("uint8"),
    window_specialization("label"),
    Extension("zipline.data._equities", ["zipline/data/_equities.pyx"]),
    Extension("zipline._protocol", ["zipline/_protocol.pyx"]),
    Extension("zipline.finance._finance_ext", ["zipline/finance/_finance_ext.pyx"]),
    Extension(
        "zipline.data._minute_bar_internal",
        ["zipline/data/_minute_bar_internal.pyx"],
    ),
]

for ext in ext_modules:
    ext.include_dirs.append(numpy.get_include())
    ext.define_macros.append(("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION"))

setup(
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={
            "language_level": "3",
            # Python-style signatures in docstrings, for help() and stubgen.
            "embedsignature": True,
            "embedsignature.format": "python",
        },
    ),
)
