"""
Utilities for validating inputs to user-facing API functions.
"""

import inspect
from functools import wraps


def preprocess(*_unused, **processors):
    """
    Decorator that applies pre-processors to the arguments of a function before
    calling the function.

    Parameters
    ----------
    **processors : dict
        Map from argument name -> processor function.

        A processor function takes three arguments: (func, argname, argvalue).

        `func` is the the function for which we're processing args.
        `argname` is the name of the argument we're processing.
        `argvalue` is the value of the argument we're processing.

    Examples
    --------
    >>> def _ensure_tuple(func, argname, arg):
    ...     if isinstance(arg, tuple):
    ...         return argvalue
    ...     try:
    ...         return tuple(arg)
    ...     except TypeError:
    ...         raise TypeError(
    ...             "%s() expected argument '%s' to"
    ...             " be iterable, but got %s instead." % (
    ...                 func.__name__, argname, arg,
    ...             )
    ...         )
    ...
    >>> @preprocess(arg=_ensure_tuple)
    ... def foo(arg):
    ...     return arg
    ...
    >>> foo([1, 2, 3])
    (1, 2, 3)
    >>> foo("a")
    ('a',)
    >>> foo(2)
    Traceback (most recent call last):
        ...
    TypeError: foo() expected argument 'arg' to be iterable, but got 2 instead.
    """
    if _unused:
        raise TypeError("preprocess() doesn't accept positional arguments")

    def _decorator(f):
        signature = inspect.signature(f)
        parameters = signature.parameters
        bad_names = processors.keys() - parameters.keys()
        if bad_names:
            raise TypeError(f"Got processors for unknown arguments: {bad_names}.")

        if all(parameters[name].kind in _NAMED for name in processors):
            return _process_named(f, parameters, processors)
        return _process_bound(f, signature, processors)

    return _decorator


# Kinds of parameters that can be passed by name.
_NAMED = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)
_POSITIONAL = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
)


def _process_named(f, parameters, processors):
    """Wrap ``f``, applying ``processors`` to arguments that can be passed by
    name, wherever they are passed.

    Each argument's position is worked out once, so a call only touches the
    processed arguments. Calls that don't fit the signature reach ``f``,
    which raises the usual TypeError.
    """
    positions = {
        name: index
        for index, (name, parameter) in enumerate(parameters.items())
        if parameter.kind in _POSITIONAL
    }
    plan = [
        (name, processor, positions.get(name), parameters[name].default)
        for name, processor in processors.items()
    ]
    empty = inspect.Parameter.empty

    @wraps(f)
    def wrapper(*args, **kwargs):
        args = list(args)
        for name, processor, position, default in plan:
            if position is not None and position < len(args):
                args[position] = processor(f, name, args[position])
            elif name in kwargs:
                kwargs[name] = processor(f, name, kwargs[name])
            elif default is not empty:
                # Processors also see the defaults of arguments not passed.
                kwargs[name] = processor(f, name, default)
        return f(*args, **kwargs)

    return wrapper


def _process_bound(f, signature, processors):
    """Wrap ``f``, applying ``processors`` to any of its arguments, e.g.
    ``*args``, by binding each call to the signature.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError as exc:
            raise TypeError(f"{f.__name__}() {exc}") from None
        # Processors also see the defaults of arguments not passed.
        bound.apply_defaults()
        for name, processor in processors.items():
            bound.arguments[name] = processor(f, name, bound.arguments[name])
        return f(*bound.args, **bound.kwargs)

    return wrapper


def call(f):
    """
    Wrap a function in a processor that calls `f` on the argument before
    passing it along.

    Useful for creating simple arguments to the `@preprocess` decorator.

    Parameters
    ----------
    f : function
        Function accepting a single argument and returning a replacement.

    Examples
    --------
    >>> @preprocess(x=call(lambda x: x + 1))
    ... def foo(x):
    ...     return x
    ...
    >>> foo(1)
    2
    """

    @wraps(f)
    def processor(func, argname, arg):
        return f(arg)

    return processor
