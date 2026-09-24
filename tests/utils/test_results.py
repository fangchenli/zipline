import pandas as pd
import pytest

from zipline.assets import Asset
from zipline.finance.execution import LimitOrder
from zipline.finance.order import ORDER_STATUS
from zipline.testing.fixtures import WithMakeAlgo, ZiplineTestCase
from zipline.testing.predicates import assert_equal
from zipline.utils.results import read_results, write_results

NESTED = ["orders", "transactions", "positions"]


def storable(dicts):
    """The dicts as write_results stores them."""
    return [
        {
            key: value.sid
            if isinstance(value, Asset)
            else value.name
            if isinstance(value, ORDER_STATUS)
            else value
            for key, value in dct.items()
        }
        for dct in dicts
    ]


class ResultsTestCase(WithMakeAlgo, ZiplineTestCase):
    START_DATE = pd.Timestamp("2006-01-03")
    END_DATE = pd.Timestamp("2006-01-10")
    ASSET_FINDER_EQUITY_SIDS = (1, 133)
    SIM_PARAMS_DATA_FREQUENCY = "daily"
    DATA_PORTAL_USE_MINUTE_DATA = False

    def init_instance_fixtures(self):
        super().init_instance_fixtures()

        def initialize(context):
            context.day = 0

        def handle_data(context, data):
            if context.day == 0:
                context.order(context.sid(1), 10)
                # Too low to fill: stays open, then is cancelled.
                context.limit_order_id = context.order(
                    context.sid(133), 5, style=LimitOrder(0.01)
                )
            elif context.day == 2:
                context.cancel_order(context.limit_order_id)
            context.record(
                day=context.day,
                label=f"day {context.day}",
                even=context.day % 2 == 0,
            )
            context.day += 1

        self.results = self.run_algorithm(
            initialize=initialize,
            handle_data=handle_data,
        )

    def test_round_trip(self):
        path = self.tmpdir.getpath("results.parquet")
        write_results(self.results, path)
        actual = read_results(path)

        assert_equal(list(actual.columns), list(self.results.columns))
        flat = [column for column in self.results.columns if column not in NESTED]
        assert_equal(actual[flat], self.results[flat])

        statuses = set()
        for column in NESTED:
            for got, expected in zip(actual[column], self.results[column]):
                expected = storable(expected)
                assert_equal(len(got), len(expected))
                for got_dict, expected_dict in zip(got, expected):
                    # Timestamps come back with datetime.timezone.utc rather
                    # than zoneinfo's UTC, so compare values rather than using
                    # assert_equal, which also compares tz objects.
                    assert {key: got_dict[key] for key in expected_dict} == (
                        expected_dict
                    )
                    # Keys a dict didn't have are stored as nulls.
                    assert all(
                        got_dict[key] is None for key in got_dict.keys() - expected_dict
                    )
                    if column == "orders":
                        statuses.add(got_dict["status"])
        assert_equal(statuses, {"OPEN", "FILLED", "CANCELLED"})

    def test_unstorable_column(self):
        results = self.results.assign(unstorable=object())
        with pytest.raises(TypeError, match="Save them as a pickle instead"):
            write_results(results, self.tmpdir.getpath("unstorable.parquet"))

    def test_unexpected_keys(self):
        results = self.results.copy()
        results["positions"] = [
            [{**position, "extra": 1} for position in positions]
            for positions in results["positions"]
        ]
        with pytest.raises(ValueError, match=r"unexpected keys \['extra'\]"):
            write_results(results, self.tmpdir.getpath("unexpected.parquet"))
