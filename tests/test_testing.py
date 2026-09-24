"""
Tests for our testing utilities.
"""

import pandas as pd
import pytest
from numpy import array, datetime64, empty

from zipline._protocol import BarData
from zipline.finance.asset_restrictions import NoRestrictions
from zipline.finance.order import Order
from zipline.testing import (
    assert_timestamp_equal,
    check_arrays,
    make_alternating_boolean_array,
    make_cascading_boolean_array,
)
from zipline.testing.fixtures import (
    WithConstantEquityMinuteBarData,
    WithDataPortal,
    WithTmpDir,
    ZiplineTestCase,
)
from zipline.testing.predicates import assert_equal, instance_of, wildcard
from zipline.testing.slippage import TestingSlippage
from zipline.utils.numpy_utils import bool_dtype


class TestMakeBooleanArray:
    def test_make_alternating_boolean_array(self):
        check_arrays(
            make_alternating_boolean_array((3, 3)),
            array([[True, False, True], [False, True, False], [True, False, True]]),
        )
        check_arrays(
            make_alternating_boolean_array((3, 3), first_value=False),
            array([[False, True, False], [True, False, True], [False, True, False]]),
        )
        check_arrays(
            make_alternating_boolean_array((1, 3)),
            array([[True, False, True]]),
        )
        check_arrays(
            make_alternating_boolean_array((3, 1)),
            array([[True], [False], [True]]),
        )
        check_arrays(
            make_alternating_boolean_array((3, 0)),
            empty((3, 0), dtype=bool_dtype),
        )

    def test_make_cascading_boolean_array(self):
        check_arrays(
            make_cascading_boolean_array((3, 3)),
            array([[True, True, False], [True, False, False], [False, False, False]]),
        )
        check_arrays(
            make_cascading_boolean_array((3, 3), first_value=False),
            array([[False, False, True], [False, True, True], [True, True, True]]),
        )
        check_arrays(
            make_cascading_boolean_array((1, 3)),
            array([[True, True, False]]),
        )
        check_arrays(
            make_cascading_boolean_array((3, 1)),
            array([[False], [False], [False]]),
        )
        check_arrays(
            make_cascading_boolean_array((3, 0)),
            empty((3, 0), dtype=bool_dtype),
        )


class TestTestingSlippage(
    WithConstantEquityMinuteBarData, WithDataPortal, ZiplineTestCase
):
    ASSET_FINDER_EQUITY_SYMBOLS = ("A",)
    ASSET_FINDER_EQUITY_SIDS = (1,)

    @classmethod
    def init_class_fixtures(cls):
        super().init_class_fixtures()
        cls.asset = cls.asset_finder.retrieve_asset(1)
        cls.minute, _ = cls.trading_calendar.session_first_last_minute(cls.START_DATE)

    def init_instance_fixtures(self):
        super().init_instance_fixtures()
        self.bar_data = BarData(
            self.data_portal,
            lambda: self.minute,
            "minute",
            self.trading_calendar,
            NoRestrictions(),
        )

    def make_order(self, amount):
        return Order(
            self.minute,
            self.asset,
            amount,
        )

    def test_constant_filled_per_tick(self):
        filled_per_tick = 1
        model = TestingSlippage(filled_per_tick)
        order = self.make_order(100)

        price, volume = model.process_order(self.bar_data, order)

        assert price == self.EQUITY_MINUTE_CONSTANT_CLOSE
        assert volume == filled_per_tick

    def test_fill_all(self):
        filled_per_tick = TestingSlippage.ALL
        order_amount = 100

        model = TestingSlippage(filled_per_tick)
        order = self.make_order(order_amount)

        price, volume = model.process_order(self.bar_data, order)

        assert price == self.EQUITY_MINUTE_CONSTANT_CLOSE
        assert volume == order_amount


class TestPredicates(ZiplineTestCase):
    def test_assert_equal_dispatch(self):
        # Mismatched types are compared with ==.
        with pytest.raises(AssertionError):
            assert_equal(1.0, "1.0")
        # Implementations registered for a pair of types, or tuples of types,
        # apply to their subclasses.
        assert_equal(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-02"))
        with pytest.raises(AssertionError):
            assert_equal(pd.Timestamp("2020-01-02"), datetime64("2020-01-02"))
        assert_equal(
            pd.Timestamp("2020-01-02"),
            datetime64("2020-01-02"),
            allow_datetime_coercions=True,
        )
        # Floats are compared with a tolerance.
        assert_equal(0.1 + 0.2, 0.3)

    def test_wildcard(self):
        for obj in 1, object(), "foo", {}:
            assert obj == wildcard
            assert [obj] == [wildcard]
            assert {"foo": wildcard} == {"foo": wildcard}

    def test_instance_of(self):
        assert 1 == instance_of(int)
        assert 1 != instance_of(str)
        assert 1 == instance_of((str, int))
        assert "foo" == instance_of((str, int))

    def test_instance_of_exact(self):

        class Foo:
            pass

        class Bar(Foo):
            pass

        assert Bar() == instance_of(Foo)
        assert Bar() != instance_of(Foo, exact=True)


class TestAssertTimestampEqual:
    def test_equal_and_nat(self):
        ts = pd.Timestamp("2020-01-02")
        assert_timestamp_equal(ts, pd.Timestamp("2020-01-02"))
        assert_timestamp_equal(pd.NaT, pd.NaT)

    def test_unequal(self):
        with pytest.raises(AssertionError) as e:
            assert_timestamp_equal(
                pd.Timestamp("2020-01-02"),
                pd.Timestamp("2020-01-03"),
                msg="context",
            )
        assert "context" in str(e.value)
        with pytest.raises(AssertionError):
            assert_timestamp_equal(pd.NaT, pd.NaT, compare_nat_equal=False)


class TestDebugMROFailure:
    def test_reports_cycle(self):
        # WithDataPortal subclasses WithTmpDir, so it can't come after it.
        with pytest.raises(TypeError) as e:

            class _Unlinearizable(WithTmpDir, WithDataPortal, ZiplineTestCase):
                pass

        message = str(e.value)
        assert "Cycle found when trying to compute MRO for _Unlinearizable" in message
        assert "WithTmpDir comes before WithDataPortal" in message
