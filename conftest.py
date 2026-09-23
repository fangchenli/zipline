import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _doctest_numpy_printing(request):
    """Run doctests with the numpy print style they were written against."""
    if not isinstance(request.node, pytest.DoctestItem):
        yield
        return

    print_options = np.get_printoptions()
    err = np.geterr()
    np.set_printoptions(legacy="1.13")
    np.seterr(all="ignore")
    try:
        yield
    finally:
        np.set_printoptions(**print_options)
        np.seterr(**err)
