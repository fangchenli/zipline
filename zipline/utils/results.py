"""Saving and loading backtest results.

``TradingAlgorithm.run`` and ``run_algorithm`` return the results as a
DataFrame with one row per session. Most columns are numbers, but ``orders``,
``transactions`` and ``positions`` hold a list of dicts per session, and their
dicts contain assets and order statuses, which Parquet can't store.
:func:`write_results` stores those lists as Parquet lists of structs, with
each asset replaced by its sid and each order status by its name. Other tools
that read Parquet (DuckDB, Polars, R and so on) can query the file directly;
:func:`read_results` reads it back into a DataFrame shaped like the original.
"""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from zipline.assets import Asset
from zipline.finance.order import ORDER_STATUS

_TIMESTAMP = pa.timestamp("ns", tz="UTC")

_ORDER = pa.struct(
    [
        ("id", pa.string()),
        ("dt", _TIMESTAMP),
        ("reason", pa.string()),
        ("created", _TIMESTAMP),
        ("amount", pa.int64()),
        ("filled", pa.int64()),
        ("commission", pa.float64()),
        ("stop", pa.float64()),
        ("limit", pa.float64()),
        ("stop_reached", pa.bool_()),
        ("limit_reached", pa.bool_()),
        ("broker_order_id", pa.string()),
        ("sid", pa.int64()),
        ("status", pa.string()),
    ]
)

_TRANSACTION = pa.struct(
    [
        ("amount", pa.int64()),
        ("dt", _TIMESTAMP),
        ("price", pa.float64()),
        ("order_id", pa.string()),
        ("sid", pa.int64()),
        ("commission", pa.float64()),
    ]
)

_POSITION = pa.struct(
    [
        ("sid", pa.int64()),
        ("amount", pa.int64()),
        ("cost_basis", pa.float64()),
        ("last_sale_price", pa.float64()),
    ]
)

# The columns of lists of dicts written by the built-in metrics, and the type
# of their dicts.
_NESTED_COLUMNS = {
    "orders": _ORDER,
    "transactions": _TRANSACTION,
    "positions": _POSITION,
}


def _storable(value):
    if isinstance(value, Asset):
        return value.sid
    if isinstance(value, ORDER_STATUS):
        return value.name
    return value


def _nested_column(name, values, struct):
    fields = {field.name for field in struct}
    rows = []
    for dicts in values:
        row = []
        for dct in dicts:
            unknown = dct.keys() - fields
            if unknown:
                raise ValueError(
                    f"Can't store the {name!r} column as Parquet: unexpected"
                    f" keys {sorted(unknown)}."
                )
            row.append({key: _storable(value) for key, value in dct.items()})
        rows.append(row)
    return pa.array(rows, type=pa.list_(struct))


def write_results(results, path):
    """Write backtest results to a Parquet file.

    Parameters
    ----------
    results : pd.DataFrame
        The results of a backtest, as returned by ``run_algorithm``.
    path : str or path-like
        The file to write.

    Raises
    ------
    TypeError
        If a column, e.g. one written with ``record``, holds values that
        Parquet can't store.

    See Also
    --------
    read_results
    """
    nested = [name for name in results.columns if name in _NESTED_COLUMNS]
    try:
        table = pa.Table.from_pandas(results.drop(columns=nested))
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise TypeError(
            f"Can't store these results as Parquet: {exc}. Save them as a"
            " pickle instead."
        ) from exc
    # The flat columns keep their order, followed by the index, so inserting
    # the nested columns in order puts them back at their positions.
    for name in nested:
        table = table.add_column(
            results.columns.get_loc(name),
            name,
            _nested_column(name, results[name], _NESTED_COLUMNS[name]),
        )
    pq.write_table(table, path, compression="zstd")


def read_results(path):
    """Read backtest results written by :func:`write_results`.

    List columns hold Python lists, of dicts for ``orders``, ``transactions``
    and ``positions``. In those dicts assets are sids and order statuses are
    names such as ``"FILLED"``.

    Parameters
    ----------
    path : str or path-like
        The file to read.

    Returns
    -------
    results : pd.DataFrame
    """
    table = pq.read_table(path)
    results = table.to_pandas()
    # to_pandas makes numpy arrays of lists, and integers of timestamps in
    # structs; to_pylist makes lists, with Timestamps.
    for field in table.schema:
        if pa.types.is_list(field.type):
            results[field.name] = pd.Series(
                table.column(field.name).to_pylist(),
                index=results.index,
                dtype=object,
            )
    return results
