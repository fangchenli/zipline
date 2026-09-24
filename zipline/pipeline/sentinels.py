from enum import Enum


class NotSpecifiedType(Enum):
    """The type of :data:`NotSpecified`.

    A single-member enum, so type checkers know ``NotSpecified`` is its only
    value, e.g. in ``mask: Filter | NotSpecifiedType = NotSpecified``.
    """

    NotSpecified = "NotSpecified"

    def __repr__(self):
        return "NotSpecified"

    __str__ = __repr__


#: Singleton sentinel value used for Term defaults.
NotSpecified = NotSpecifiedType.NotSpecified
