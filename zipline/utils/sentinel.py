"""
Construction of sentinel objects.

Sentinel objects are used when you only care to check for object identity.
"""

import sys
from textwrap import dedent


class _Sentinel:
    """Base class for Sentinel objects."""

    __slots__ = ("__weakref__",)


# Sentinels created so far, by name.
_cache = {}


def is_sentinel(obj):
    return isinstance(obj, _Sentinel)


def sentinel(name, doc=None):
    try:
        value = _cache[name]  # memoized
    except KeyError:
        pass
    else:
        if doc == value.__doc__:
            return value

        raise ValueError(
            dedent(
                """\
            New sentinel value %r conflicts with an existing sentinel of the
            same name.
            Old sentinel docstring: %r
            New sentinel docstring: %r

            The old sentinel was created at: %s

            Resolve this conflict by changing the name of one of the sentinels.
            """,
            )
            % (name, value.__doc__, doc, value._created_at)
        )

    try:
        frame = sys._getframe(1)
    except ValueError:
        frame = None

    if frame is None:
        created_at = "<unknown>"
    else:
        created_at = f"{frame.f_code.co_filename}:{frame.f_lineno}"

    @object.__new__  # bind a single instance to the name 'Sentinel'
    class Sentinel(_Sentinel):
        __doc__ = doc
        __name__ = name

        # store created_at so that we can report this in case of a duplicate
        # name violation
        _created_at = created_at

        def __new__(cls):
            raise TypeError(f"cannot create {name!r} instances")

        def __repr__(self):
            return f"sentinel({name!r})"

        def __reduce__(self):
            return sentinel, (name, doc)

        def __deepcopy__(self, _memo):
            return self

        def __copy__(self):
            return self

    # Report the calling module as the sentinel's module, or None if it can't
    # be determined.
    module = frame.f_globals.get("__name__") if frame is not None else None
    type(Sentinel).__module__ = module  # ty: ignore[invalid-assignment]

    _cache[name] = Sentinel  # cache result
    return Sentinel
