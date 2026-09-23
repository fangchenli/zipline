"""dispatcher object with a custom namespace.

Anything that has been dispatched will also be put into this module.
"""

from functools import partial

from multipledispatch import dispatch

dispatch = partial(dispatch, namespace=globals())

del partial
