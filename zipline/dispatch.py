"""dispatcher object with a custom namespace.

Anything that has been dispatched will also be put into this module.
"""

import sys
from functools import partial

from multipledispatch import dispatch

try:
    from datashape.dispatch import namespace
except ImportError:
    pass
else:
    globals().update(namespace)
    del namespace

dispatch = partial(dispatch, namespace=globals())

del partial
del sys
