"""
Tools for memoization of function results.
"""

from _thread import allocate_lock
from collections import OrderedDict
from collections.abc import Callable, Sequence
from functools import wraps
from itertools import compress
from weakref import WeakKeyDictionary, ref

from toolz.sandbox import unzip


def _weak_lru_cache(maxsize=100):
    """
    Users should only access the lru_cache through its public API:
    cache_info, cache_clear
    The internals of the lru_cache are encapsulated for thread safety and
    to allow the implementation to change.
    """

    def decorating_function(
        user_function, tuple=tuple, sorted=sorted, len=len, KeyError=KeyError
    ):

        hits, misses = [0], [0]
        kwd_mark = (object(),)  # separates positional and keyword args
        lock = allocate_lock()  # needed because OrderedDict isn't threadsafe

        if maxsize is None:
            cache = _WeakArgsDict()  # cache without ordering or size limit

            @wraps(user_function)
            def wrapper(*args, **kwds):
                key = args
                if kwds:
                    key += kwd_mark + tuple(sorted(kwds.items()))
                try:
                    result = cache[key]
                    hits[0] += 1
                    return result
                except KeyError:
                    pass
                result = user_function(*args, **kwds)
                cache[key] = result
                misses[0] += 1
                return result
        else:
            # ordered least recent to most recent
            cache = _WeakArgsOrderedDict()
            cache_popitem = cache.popitem
            cache_renew = cache.move_to_end

            @wraps(user_function)
            def wrapper(*args, **kwds):
                key = args
                if kwds:
                    key += kwd_mark + tuple(sorted(kwds.items()))
                with lock:
                    try:
                        result = cache[key]
                        cache_renew(key)  # record recent use of this key
                        hits[0] += 1
                        return result
                    except KeyError:
                        pass
                result = user_function(*args, **kwds)
                with lock:
                    cache[key] = result  # record recent use of this key
                    misses[0] += 1
                    if len(cache) > maxsize:
                        # purge least recently used cache entry
                        cache_popitem(False)
                return result

        def cache_info():
            """Report cache statistics"""
            with lock:
                return hits[0], misses[0], maxsize, len(cache)

        def cache_clear():
            """Clear the cache and cache statistics"""
            with lock:
                cache.clear()
                hits[0] = misses[0] = 0

        # Mirror functools.lru_cache's API on the wrapper function.
        wrapper.cache_info = cache_info  # ty: ignore[unresolved-attribute]
        wrapper.cache_clear = cache_clear  # ty: ignore[unresolved-attribute]
        return wrapper

    return decorating_function


class _WeakArgs(Sequence):
    """
    Works with _WeakArgsDict to provide a weak cache for function args.
    When any of those args are gc'd, the pair is removed from the cache.
    """

    def __init__(self, items, dict_remove=None):
        selfref = ref(self)

        def remove(k):
            self_ = selfref()
            if self_ is not None and dict_remove is not None:
                dict_remove(self_)

        self._items, self._selectors = unzip(
            self._try_ref(item, remove) for item in items
        )
        self._items = tuple(self._items)
        self._selectors = tuple(self._selectors)

    def __getitem__(self, index):
        return self._items[index]

    def __len__(self):
        return len(self._items)

    @staticmethod
    def _try_ref(item, callback):
        try:
            return ref(item, callback), True
        except TypeError:
            return item, False

    @property
    def alive(self):
        return all(
            item() is not None for item in compress(self._items, self._selectors)
        )

    def __eq__(self, other):
        return self._items == other._items

    def __hash__(self):
        try:
            return self.__hash
        except AttributeError:
            h = self.__hash = hash(self._items)
            return h


class _WeakArgsDict(WeakKeyDictionary):
    # WeakKeyDictionary internals that this subclass builds on.
    data: dict
    _remove: Callable

    def __delitem__(self, key):
        del self.data[_WeakArgs(key)]

    def __getitem__(self, key):
        return self.data[_WeakArgs(key)]

    def __repr__(self):
        return f"{type(self).__name__}({self.data!r})"

    def __setitem__(self, key, value):
        self.data[_WeakArgs(key, self._remove)] = value

    def __contains__(self, key):
        try:
            wr = _WeakArgs(key)
        except TypeError:
            return False
        return wr in self.data

    def pop(self, key, *args):  # ty: ignore[invalid-method-override]
        # Same behaviour as WeakKeyDictionary.pop(key[, default]).
        return self.data.pop(_WeakArgs(key), *args)


class _WeakArgsOrderedDict(_WeakArgsDict):
    data: OrderedDict

    def __init__(self):
        super().__init__()
        self.data = OrderedDict()

    def popitem(self, last=True):
        while True:
            key, value = self.data.popitem(last)
            if key.alive:
                return tuple(key), value

    def move_to_end(self, key):
        """Move an existing element to the end.

        Raises KeyError if the element does not exist.
        """
        self[key] = self.pop(key)


def weak_lru_cache(maxsize=100):
    """Weak least-recently-used cache decorator.

    If *maxsize* is set to None, the LRU features are disabled and the cache
    can grow without bound.

    Arguments to the cached function must be hashable. Any that are weak-
    referenceable will be stored by weak reference.  Once any of the args have
    been garbage collected, the entry will be removed from the cache.

    View the cache statistics named tuple (hits, misses, maxsize, currsize)
    with f.cache_info().  Clear the cache and statistics with f.cache_clear().

    See:  http://en.wikipedia.org/wiki/Cache_algorithms#Least_Recently_Used

    """

    class desc:
        def __init__(self, get):
            self._get = get
            # A cached function per instance.
            self._cache = WeakKeyDictionary()

        def __get__(self, instance, owner):
            if instance is None:
                return self
            try:
                return self._cache[instance]
            except KeyError:
                inst = ref(instance)

                @_weak_lru_cache(maxsize)
                @wraps(self._get)
                def wrapper(*args, **kwargs):
                    return self._get(inst(), *args, **kwargs)

                self._cache[instance] = wrapper
                return wrapper

        @_weak_lru_cache(maxsize)
        def __call__(self, *args, **kwargs):
            return self._get(*args, **kwargs)

    return desc


remember_last = weak_lru_cache(1)
