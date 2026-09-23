from zipline.testing.fixtures import ZiplineTestCase
from zipline.testing.predicates import (
    assert_equal,
    assert_is,
    assert_is_subclass,
)
from zipline.utils.metautils import compose_types


class C:
    @staticmethod
    def f():
        return 'C.f'

    def delegate(self):
        return 'C.delegate', super().delegate()


class D:
    @staticmethod
    def f():
        return 'D.f'

    @staticmethod
    def g():
        return 'D.g'

    def delegate(self):
        return 'D.delegate'


class ComposeTypesTestCase(ZiplineTestCase):

    def test_identity(self):
        assert_is(
            compose_types(C),
            C,
            msg='compose_types of a single class should be identity',
        )

    def test_compose(self):
        composed = compose_types(C, D)

        assert_is_subclass(composed, C)
        assert_is_subclass(composed, D)

    def test_compose_mro(self):
        composed = compose_types(C, D)

        assert_equal(composed.f(), C.f())
        assert_equal(composed.g(), D.g())

        assert_equal(composed().delegate(), ('C.delegate', 'D.delegate'))

