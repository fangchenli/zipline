import inspect
from math import ceil


def consistent_round(val):
    if (val % 1) >= 0.5:
        return ceil(val)
    else:
        return round(val)


def values_as_list(dictionary):
    """Return the dictionary values as a list without forcing a copy
    in Python 2.
    """
    return list(dictionary.values())


def getargspec(f):
    full_argspec = inspect.getfullargspec(f)
    return inspect.ArgSpec(
        args=full_argspec.args,
        varargs=full_argspec.varargs,
        keywords=full_argspec.varkw,
        defaults=full_argspec.defaults,
    )


__all__ = [
    "consistent_round",
    "values_as_list",
]
