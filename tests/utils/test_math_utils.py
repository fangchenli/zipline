from unittest import TestCase

from zipline.utils.math_utils import number_of_decimal_places


class MathUtilsTestCase(TestCase):
    def test_number_of_decimal_places(self):
        assert number_of_decimal_places(1) == 0
        assert number_of_decimal_places(3.14) == 2
        assert number_of_decimal_places("3.14") == 2
        assert number_of_decimal_places(-3.14) == 2
