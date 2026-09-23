from operator import attrgetter


def compose_types(a, *cs):
    """Compose multiple classes together.

    Parameters
    ----------
    *mcls : tuple[type]
        The classes that you would like to compose

    Returns
    -------
    cls : type
        A type that subclasses all of the types in ``mcls``.

    Notes
    -----
    A common use case for this is to build composed metaclasses, for example,
    imagine you have some simple metaclass ``M`` and some instance of ``M``
    named ``C`` like so:

    .. code-block:: python

       >>> class M(type):
       ...     def __new__(mcls, name, bases, dict_):
       ...         dict_['ayy'] = 'lmao'
       ...         return super(M, mcls).__new__(mcls, name, bases, dict_)


       >>> class C(metaclass=M):
       ...     pass


    We now want to create a sublclass of ``C`` that is also an abstract class.
    We can use ``compose_types`` to create a new metaclass that is a subclass
    of ``M`` and ``ABCMeta``. This is needed because a subclass of a class
    with a metaclass must have a metaclass which is a subclass of the metaclass
    of the superclass.


    .. code-block:: python

       >>> from abc import ABCMeta, abstractmethod
       >>> class D(C, metaclass=compose_types(M, ABCMeta)):
       ...     @abstractmethod
       ...     def f(self):
       ...         raise NotImplementedError('f')


    We can see that this class has both metaclasses applied to it:

    .. code-block:: python

       >>> D.ayy
       'lmao'
       >>> D()  # doctest: +ELLIPSIS
       Traceback (most recent call last):
          ...
       TypeError: Can't instantiate abstract class D ...


    An important note here is that ``M`` did not use ``type.__new__`` and
    instead used ``super()``. This is to support cooperative multiple
    inheritance which is needed for ``compose_types`` to work as intended.
    After we have composed these types ``M.__new__``\'s super will actually
    go to ``ABCMeta.__new__`` and not ``type.__new__``.

    Always using ``super()`` to dispatch to your superclass is best practices
    anyways so most classes should compose without much special considerations.
    """
    if not cs:
        # if there are no types to compose then just return the single type
        return a

    mcls = (a,) + cs
    return type(
        "compose_types(%s)" % ", ".join(map(attrgetter("__name__"), mcls)),
        mcls,
        {},
    )
