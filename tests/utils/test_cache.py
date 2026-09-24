from unittest import TestCase

import pytest
from pandas import Timedelta, Timestamp

from zipline.utils.cache import CachedObject, Expired, ExpiringCache, LRUCache


class CachedObjectTestCase(TestCase):
    def test_cached_object(self):
        expiry = Timestamp("2014")
        before = expiry - Timedelta("1 minute")
        after = expiry + Timedelta("1 minute")

        obj = CachedObject(1, expiry)

        assert obj.unwrap(before) == 1
        assert obj.unwrap(expiry) == 1  # Unwrap on expiry is allowed.
        with pytest.raises(Expired) as e:
            obj.unwrap(after)
        assert e.value.args == (expiry,)

    def test_expired(self):
        always_expired = CachedObject.expired()

        for dt in Timestamp.min, Timestamp.now(), Timestamp.max:
            with pytest.raises(Expired):
                always_expired.unwrap(dt)


class ExpiringCacheTestCase(TestCase):
    def test_expiring_cache(self):
        expiry_1 = Timestamp("2014")
        before_1 = expiry_1 - Timedelta("1 minute")
        after_1 = expiry_1 + Timedelta("1 minute")

        expiry_2 = Timestamp("2015")
        after_2 = expiry_1 + Timedelta("1 minute")

        expiry_3 = Timestamp("2016")

        cache = ExpiringCache()

        cache.set("foo", 1, expiry_1)
        cache.set("bar", 2, expiry_2)

        assert cache.get("foo", before_1) == 1
        # Unwrap on expiry is allowed.
        assert cache.get("foo", expiry_1) == 1

        with pytest.raises(KeyError) as e:
            cache.get("foo", after_1)
        assert e.value.args == ("foo",)

        # Should raise same KeyError after deletion.
        with pytest.raises(KeyError) as e:
            cache.get("foo", before_1)
        assert e.value.args == ("foo",)

        # Second value should still exist.
        assert cache.get("bar", after_2) == 2

        # Should raise similar KeyError on non-existent key.
        with pytest.raises(KeyError) as e:
            cache.get("baz", expiry_3)
        assert e.value.args == ("baz",)


class LRUCacheTestCase(TestCase):
    def test_evicts_least_recently_used(self):
        cache = LRUCache(2)
        cache["a"] = 1
        cache["b"] = 2
        # Reading "a" makes "b" the least recently used.
        assert cache["a"] == 1
        cache["c"] = 3
        assert dict(cache) == {"a": 1, "c": 3}
        # So does overwriting it.
        cache["a"] = 4
        cache["d"] = 5
        assert dict(cache) == {"a": 4, "d": 5}
        del cache["a"]
        assert len(cache) == 1

    def test_maxsize(self):
        with pytest.raises(ValueError):
            LRUCache(0)
