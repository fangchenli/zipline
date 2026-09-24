"""Helpers shared by the Parquet bar formats."""

import json
import os
import warnings

import numpy as np
import pandas as pd

PRICE_FIELDS = ("open", "high", "low", "close")
FIELDS = PRICE_FIELDS + ("volume",)


# Side files start with "_", which dataset readers (pyarrow, pandas, DuckDB,
# Spark) skip, so the bar partitions can be read as a plain dataset.


def metadata_path(rootdir):
    return os.path.join(rootdir, "_metadata.json")


def assets_path(rootdir):
    return os.path.join(rootdir, "_assets.parquet")


def write_metadata(rootdir, metadata):
    with open(metadata_path(rootdir), "w") as f:
        json.dump(metadata, f, indent=2)


def directory_has_files(rootdir):
    """Whether ``rootdir`` exists and contains anything."""
    if not os.path.isdir(rootdir):
        return False
    with os.scandir(rootdir) as entries:
        return next(entries, None) is not None


def read_metadata(rootdir, format_name, format_version, description):
    """Read a dataset's metadata, checking that this version can read it."""
    path = metadata_path(rootdir)
    if not os.path.exists(path):
        raise ValueError(
            f"{rootdir} does not contain a {description} dataset: "
            f"{os.path.basename(path)} is missing"
        )
    with open(path) as f:
        metadata = json.load(f)
    if metadata.get("format") != format_name:
        raise ValueError(f"{rootdir} is not a {description} dataset")
    if metadata["version"] > format_version:
        raise ValueError(
            f"{rootdir} was written with format version {metadata['version']}"
            f", but this version of zipline reads up to {format_version}"
        )
    return metadata


def handle_invalid(sid, labels, values, invalid, invalid_data_behavior):
    """Report negative or infinite ``values``, labelled by ``labels``."""
    if invalid_data_behavior == "ignore":
        return
    rows = invalid.any(axis=1)
    bad = pd.DataFrame(
        values[rows], index=labels[: len(values)][rows], columns=list(FIELDS)
    )
    message = (
        f"Ignoring {int(invalid.sum())} negative or infinite values for sid {sid}:"
        f"\n{bad}"
    )
    if invalid_data_behavior == "raise":
        raise ValueError(message)
    warnings.warn(message, stacklevel=4)


def epoch_nanos(index):
    """Nanoseconds since the epoch of a DatetimeIndex, as int64.

    Naive timestamps are taken as UTC.
    """
    return index.to_numpy(dtype="datetime64[ns]").view("int64")


def as_sids(assets):
    """Integer sids for a sequence of sids or Asset objects.

    Callers such as the DataPortal pass Assets, which hash and compare like
    their sids but aren't matched against integer pandas indexes.
    """
    return np.fromiter((int(asset) for asset in assets), dtype="int64")
