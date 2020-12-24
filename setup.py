#!/usr/bin/env python
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
import os
import re
import sys
from operator import lt, gt, eq, le, ge
from os.path import (
    abspath,
    dirname,
    join,
)
from distutils.version import StrictVersion

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
import numpy
import versioneer


cmdclass = versioneer.get_cmdclass()
cmdclass["build_ext"] = build_ext


def window_specialization(typename):
    """Make an extension for an AdjustedArrayWindow specialization."""
    return Extension(
        f'zipline.lib._{typename}window',
        [f'zipline/lib/_{typename}window.pyx'],
        depends=['zipline/lib/_windowtemplate.pxi'],
    )


ext_modules = [
    Extension('zipline.assets._assets', ['zipline/assets/_assets.pyx']),
    Extension('zipline.assets.continuous_futures',
              ['zipline/assets/continuous_futures.pyx']),
    Extension('zipline.lib.adjustment', ['zipline/lib/adjustment.pyx']),
    Extension('zipline.lib._factorize', ['zipline/lib/_factorize.pyx']),
    window_specialization('float64'),
    window_specialization('int64'),
    window_specialization('int64'),
    window_specialization('uint8'),
    window_specialization('label'),
    Extension('zipline.lib.rank', ['zipline/lib/rank.pyx']),
    Extension('zipline.data._equities', ['zipline/data/_equities.pyx']),
    Extension('zipline.data._adjustments', ['zipline/data/_adjustments.pyx']),
    Extension('zipline._protocol', ['zipline/_protocol.pyx']),
    Extension(
        'zipline.finance._finance_ext',
        ['zipline/finance/_finance_ext.pyx'],
    ),
    Extension('zipline.gens.sim_engine', ['zipline/gens/sim_engine.pyx']),
    Extension(
        'zipline.data._minute_bar_internal',
        ['zipline/data/_minute_bar_internal.pyx']
    ),
    Extension(
        'zipline.data._resample',
        ['zipline/data/_resample.pyx']
    ),
]

numpy_include = numpy.get_include()
for ext in ext_modules:
    ext.cython_directives = {"language_level": "3"}
    ext.include_dirs.append(numpy_include)
    ext.define_macros.append(("NPY_NO_DEPRECATED_API", "0"))


STR_TO_CMP = {
    '<': lt,
    '<=': le,
    '=': eq,
    '==': eq,
    '>': gt,
    '>=': ge,
}

SYS_VERSION = '.'.join(list(map(str, sys.version_info[:3])))


def _filter_requirements(lines_iter):
    for line in lines_iter:
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        match = REQ_PATTERN.match(line)
        if match is None:
            raise AssertionError("Could not parse requirement: %r" % line)

        name = match.group('name')
        if match.group('pyspec'):
            pycomp, pyspec = match.group('pycomp', 'pyspec')
            comp = STR_TO_CMP[pycomp]
            pyver_spec = StrictVersion(pyspec)
            if comp(SYS_VERSION, pyver_spec):
                # pip install -r understands lines with ;python_version<'3.0',
                # but pip install -e does not.  Filter here, removing the
                # env marker.
                yield line.split(';')[0]
            continue

        yield line


REQ_PATTERN = re.compile(
    r"(?P<name>[^=<>;]+)((?P<comp>[<=>]{1,2})(?P<spec>[^;]+))?"
    r"(?:(;\W*python_version\W*(?P<pycomp>[<=>]{1,2})\W*"
    r"(?P<pyspec>[0-9.]+)))?\W*"
)


def read_requirements(path):
    """
    Read a requirements file, expressed as a path relative to Zipline root.
    """
    real_path = join(dirname(abspath(__file__)), path)
    with open(real_path) as f:
        reqs = _filter_requirements(f.readlines())
        return list(reqs)


setup(
    version=versioneer.get_version(),
    cmdclass=cmdclass,
    ext_modules=ext_modules,
    install_requires=read_requirements('requirements-dev.txt'),
)
