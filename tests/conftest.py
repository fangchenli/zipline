import inspect


def pytest_pycollect_makeitem(collector, name, obj):
    """Skip test classes whose names start with an underscore.

    nose treated these as private, and the suite uses the convention for
    abstract base test cases (e.g. ``_DailyBarsTestCase``).
    """
    if inspect.isclass(obj) and name.startswith("_"):
        return []
    return None


def pytest_collection_modifyitems(items):
    """Reject parametrizing over a set.

    pytest-xdist workers must collect tests in the same order, and a set's
    order can differ between processes, so parametrize over a list (e.g.
    ``sorted(some_set)``) instead.
    """
    for item in items:
        for mark in item.iter_markers("parametrize"):
            values = mark.args[1] if len(mark.args) > 1 else mark.kwargs["argvalues"]
            if isinstance(values, (set, frozenset)):
                raise TypeError(
                    f"{item.nodeid}: parametrize {mark.args[0]!r} over a list, "
                    "not a set, so that every xdist worker collects tests in "
                    "the same order."
                )
