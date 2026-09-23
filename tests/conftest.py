import inspect


def pytest_pycollect_makeitem(collector, name, obj):
    """Skip test classes whose names start with an underscore.

    nose treated these as private, and the suite uses the convention for
    abstract base test cases (e.g. ``_DailyBarsTestCase``).
    """
    if inspect.isclass(obj) and name.startswith("_"):
        return []
    return None
