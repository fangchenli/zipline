import numpy as np
import pytest

from zipline.lib.rank import (
    grouped_masked_is_maximal,
    masked_rankdata_2d,
    rankdata_1d_ascending,
    rankdata_1d_descending,
)

nan = np.nan
METHODS = ["ordinal", "average", "min", "max", "dense"]


@pytest.mark.parametrize("method", METHODS)
def test_missing_values_are_left_out(method):
    # Missing or masked-out values get NaN and don't count in the others'
    # ranks, for every method (scipy's rankdata makes the whole row NaN).
    data = np.array([[3.0, nan, 1.0, 2.0], [4.0, 5.0, 6.0, 7.0]])
    mask = np.array([[True, True, True, True], [True, False, True, True]])
    result = masked_rankdata_2d(data, mask, nan, method, ascending=True)
    np.testing.assert_array_equal(result, [[3, nan, 1, 2], [1, nan, 2, 3]])
    result = masked_rankdata_2d(data, mask, nan, method, ascending=False)
    np.testing.assert_array_equal(result, [[1, nan, 3, 2], [3, nan, 2, 1]])


def test_ties():
    data = np.array([[2.0, 1.0, 2.0, 3.0]])
    mask = np.ones(data.shape, dtype=bool)
    expected = {
        "ordinal": [2, 1, 3, 4],
        "average": [2.5, 1, 2.5, 4],
        "min": [2, 1, 2, 4],
        "max": [3, 1, 3, 4],
        "dense": [2, 1, 2, 3],
    }
    for method, ranks in expected.items():
        result = masked_rankdata_2d(data, mask, nan, method, ascending=True)
        np.testing.assert_array_equal(result, [ranks])


def test_integers_and_datetimes():
    # Ranked by value, including negative integers and values too large to
    # be exact as floats.
    ints = np.array([[-5, 3, 2**62 + 1, 2**62, -1]], dtype="int64")
    mask = np.ones(ints.shape, dtype=bool)
    result = masked_rankdata_2d(ints, mask, 0, "ordinal", ascending=True)
    np.testing.assert_array_equal(result, [[1, 3, 5, 4, 2]])

    dts = np.array([["2020-01-02", "NaT", "1969-12-31", "2020-01-01"]], "M8[ns]")
    mask = np.ones(dts.shape, dtype=bool)
    result = masked_rankdata_2d(dts, mask, np.datetime64("NaT"), "min", False)
    np.testing.assert_array_equal(result, [[1, nan, 3, 2]])


def test_1d():
    data = np.array([3.0, nan, 1.0, 1.0])
    np.testing.assert_array_equal(
        rankdata_1d_ascending(data, "average"), [3, nan, 1.5, 1.5]
    )
    np.testing.assert_array_equal(
        rankdata_1d_descending(data, "average"), [1, nan, 2.5, 2.5]
    )
    dts = np.array(["2020-01-02", "NaT", "2020-01-01"], dtype="M8[ns]")
    np.testing.assert_array_equal(rankdata_1d_descending(dts, "ordinal"), [1, nan, 2])


def test_bad_dtype():
    with pytest.raises(TypeError, match="Can't compute rankdata"):
        masked_rankdata_2d(
            np.array([["a"]], dtype=object), np.ones((1, 1), bool), None, "min", True
        )


def test_grouped_masked_is_maximal():
    data = np.array(
        [
            [-3.0, -1.0, -2.0, 5.0, 5.0],
            [1.0, 9.0, 2.0, 2.0, 0.0],
        ]
    )
    groupby = np.array([[0, 0, 0, 1, 1], [7, 7, 3, 3, 3]], dtype="int64")
    mask = np.array([[True] * 5, [True, False, True, True, True]])
    # The largest value per row and group, even if negative; of equal ones,
    # the first; masked-out values aren't candidates.
    np.testing.assert_array_equal(
        grouped_masked_is_maximal(data, groupby, mask),
        [
            [False, True, False, True, False],
            [True, False, True, False, False],
        ],
    )
    np.testing.assert_array_equal(
        grouped_masked_is_maximal(data, groupby, np.zeros(data.shape, bool)),
        np.zeros(data.shape, bool),
    )
