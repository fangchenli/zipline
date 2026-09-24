"""FX rates stored as a Parquet dataset.

A dataset holds one or more "rates", each a collection of exchange rates
converting "base" currencies into "quote" currencies. The rate names are
arbitrary and user-defined, e.g. "bid", "mid" and "ask", or "london_close" and
"nyse_close"; Pipeline API users select one with ``BoundColumn.fx``.

On-disk layout, under a root directory::

    _metadata.json  format name and version
    data.parquet    the rates

Rates are stored in long format with columns ``rate`` (string), ``quote``
(string), ``base`` (string), ``dt`` (timestamp[ns, UTC]) and ``value``
(float64, null if unknown). Each ``dt`` is the point in time from which a rate
applies. Rows are sorted by rate and quote, so reading one (rate, quote) pair
only touches its row groups. Every pair covers the same dates and currencies.
"""

import os
from functools import cached_property

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from zipline.data._parquet import (
    directory_has_files,
    epoch_nanos,
    read_metadata,
    write_metadata,
)

from .base import DEFAULT_FX_RATE, FXRateReader
from .utils import as_utc, check_dts, is_sorted_ascending

FORMAT_NAME = "zipline.fx_rates.parquet"
FORMAT_VERSION = 1

SCHEMA = pa.schema(
    [
        ("rate", pa.string()),
        ("quote", pa.string()),
        ("base", pa.string()),
        ("dt", pa.timestamp("ns", tz="UTC")),
        ("value", pa.float64()),
    ]
)


def _data_path(rootdir):
    return os.path.join(rootdir, "data.parquet")


class ParquetFXRateWriter:
    """Write FX rates to a Parquet dataset read by ParquetFXRateReader.

    Parameters
    ----------
    rootdir : str
        The directory to write the dataset into. It must be empty or missing.
    """

    def __init__(self, rootdir):
        self._rootdir = rootdir

    def write(self, dts, currencies, data):
        """Write rates.

        Parameters
        ----------
        dts : pd.DatetimeIndex
            The points in time the rates apply from, in ascending order. Naive
            timestamps are taken as UTC.
        currencies : np.array[object]
            The base currencies, as ISO-4217 codes.
        data : iterator[(str, str, np.array[float64])]
            Iterator of (rate, quote_currency, array) tuples. Each array has
            shape ``(len(dts), len(currencies))``, and its columns are the
            rates converting each base currency into ``quote_currency``.
        """
        if directory_has_files(self._rootdir):
            raise ValueError(f"{self._rootdir} is not empty")
        dts = pd.DatetimeIndex(dts)
        if not is_sorted_ascending(dts):
            raise ValueError("dts is not sorted")
        for currency in currencies:
            if not isinstance(currency, str) or len(currency) != 3:
                raise ValueError(f"Invalid currency: {currency!r}")
        currencies = np.asarray(currencies, dtype=object)

        nanos = epoch_nanos(dts)
        expected_shape = (len(dts), len(currencies))
        tables = []
        for rate, quote, array in sorted(data, key=lambda item: item[:2]):
            array = np.asarray(array, dtype="float64")
            if array.shape != expected_shape:
                raise ValueError(
                    f"Unexpected shape for rate={rate}, quote={quote}."
                    f"\nExpected shape: {expected_shape}. Got {array.shape}."
                )
            n = array.size
            tables.append(
                pa.table(
                    {
                        "rate": pa.array([rate] * n, pa.string()),
                        "quote": pa.array([quote] * n, pa.string()),
                        # Row-major: each date's rates for every currency.
                        "base": np.tile(currencies, len(dts)),
                        "dt": pa.array(np.repeat(nanos, len(currencies))).cast(
                            SCHEMA.field("dt").type
                        ),
                        "value": pa.array(array.ravel(), from_pandas=True),
                    },
                    schema=SCHEMA,
                )
            )
        table = pa.concat_tables(tables) if tables else SCHEMA.empty_table()

        os.makedirs(self._rootdir, exist_ok=True)
        pq.write_table(table, _data_path(self._rootdir), compression="zstd")
        write_metadata(
            self._rootdir, {"format": FORMAT_NAME, "version": FORMAT_VERSION}
        )


class ParquetFXRateReader(FXRateReader):
    """An FXRateReader backed by a Parquet dataset.

    Parameters
    ----------
    rootdir : str
        A dataset written by :class:`ParquetFXRateWriter`.
    default_rate : str
        Rate to use when ``get_rates`` is called requesting the default rate.

    Notes
    -----
    Each (rate, quote) pair is read on first use and kept in memory as a
    (dates x currencies) array.
    """

    def __init__(self, rootdir, default_rate):
        self._rootdir = rootdir
        self._default_rate = default_rate
        self._tables = {}

    @cached_property
    def _path(self):
        """The data file, once the dataset's format has been checked."""
        read_metadata(self._rootdir, FORMAT_NAME, FORMAT_VERSION, "FX rate")
        return _data_path(self._rootdir)

    def _table(self, rate, quote):
        """``(dts, currencies, rates)`` for converting into ``quote``.

        ``rates`` has an extra last row and column of NaN, which the -1
        indices of dts before the data and of unknown currencies select.
        """
        key = (rate, quote)
        try:
            return self._tables[key]
        except KeyError:
            pass
        table = pq.read_table(
            self._path,
            columns=["base", "dt", "value"],
            filters=(pc.field("rate") == rate) & (pc.field("quote") == quote),
        )
        if table.num_rows == 0:
            raise ValueError(
                f"FX rates not available for rate={rate}, quote_currency={quote}."
            )
        frame = table.to_pandas().pivot(index="dt", columns="base", values="value")
        dts = pd.DatetimeIndex(frame.index).as_unit("ns")
        if dts.tz is None:
            dts = dts.tz_localize("UTC")
        values = frame.to_numpy(dtype="float64")
        padded = np.full((values.shape[0] + 1, values.shape[1] + 1), np.nan)
        padded[:-1, :-1] = values
        result = self._tables[key] = (
            dts,
            pd.Index(frame.columns.to_numpy(dtype=object)),
            padded,
        )
        return result

    def get_rates(self, rate, quote, bases, dts):
        """Get rates to convert ``bases`` into ``quote``.

        See :class:`zipline.data.fx.base.FXRateReader` for details.
        """
        if rate == DEFAULT_FX_RATE:
            rate = self._default_rate

        dts = as_utc(dts)
        check_dts(dts)

        table_dts, currencies, rates = self._table(rate, quote)
        # The latest rate at or before each dt; -1 before the first date.
        rows = table_dts.searchsorted(dts, side="right") - 1
        cols = currencies.get_indexer(bases)
        return rates[rows][:, cols]
