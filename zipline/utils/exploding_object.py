class NamedExplodingObject:
    """An object which has no attributes but produces a more informative
    error message when accessed.

    Parameters
    ----------
    name : str
        The name of the object. This will appear in the error messages.

    Notes
    -----
    One common use for this object is so ensure that an attribute always exists
    even if sometimes it should not be used.
    """

    def __init__(self, name, extra_message=None):
        self._name = name
        self._extra_message = extra_message

    def _explode(self, what):
        message = f"attempted to access {what} of ExplodingObject {self._name!r}"
        if self._extra_message is not None:
            message += " " + self._extra_message
        raise AttributeError(message)

    def __getattr__(self, attr):
        self._explode(f"attribute {attr!r}")

    def __getitem__(self, key):
        self._explode(f"item {key!r}")

    def __repr__(self):
        return "{}({!r}{})".format(
            type(self).__name__,
            self._name,
            # show that there is an extra message but truncate it to be
            # more readable when debugging
            ", extra_message=..." if self._extra_message is not None else "",
        )
