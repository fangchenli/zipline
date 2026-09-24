import subprocess
import sys
from textwrap import dedent

# Run in a fresh interpreter, with imports of the optional bcolz support (and
# of h5py, which zipline no longer uses) failing as they would when the
# `bcolz` extra isn't installed.
SCRIPT = dedent(
    """
    import importlib.abc
    import sys

    class BlockBcolz(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] in ("bcolz", "intervaltree", "tables", "h5py"):
                raise ImportError(f"{name} is blocked")

    sys.meta_path.insert(0, BlockBcolz())

    import zipline
    import zipline.__main__
    import zipline.api
    import zipline.data.bundles
    import zipline.data.data_portal
    import zipline.data.parquet_daily_bars
    import zipline.data.parquet_minute_bars
    import zipline.data.resample
    import zipline.testing.fixtures

    legacy = sorted(
        name
        for name in sys.modules
        if name.startswith(("zipline.data.bcolz", "zipline.data.convert_bcolz"))
    )
    assert not legacy, legacy
    """
)


def test_core_imports_without_bcolz():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
