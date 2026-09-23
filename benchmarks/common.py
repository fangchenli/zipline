import os

import numpy as np

from .data import BACKENDS, Bundle, build_bundle


class BundleBenchmark:
    """Base class for benchmarks that read the synthetic bundle.

    asv runs ``setup_cache`` once per environment, in a directory that is kept
    for the benchmarks' lifetime, so every backend's bundle is written once and
    shared by all parameter combinations.
    """

    # Writing the bundle is excluded from the timings, but give it room.
    timeout = 600

    def setup_cache(self):
        roots = {}
        for backend in BACKENDS:
            root = os.path.abspath(os.path.join("bundles", backend))
            build_bundle(root, backend)
            roots[backend] = root
        return roots

    @staticmethod
    def bundle(roots, backend):
        return Bundle(roots[backend], backend)


def random_choices(values, n, seed=0):
    """``n`` deterministic random picks from ``values``."""
    rng = np.random.default_rng(seed)
    return [values[i] for i in rng.integers(0, len(values), n)]
