"""Convert bar data from the bcolz formats to the Parquet formats.

Zipline used to store daily and minute bars with bcolz. These functions
rewrite such datasets with the Parquet writers, so bundles ingested by
earlier versions (which may no longer be possible to re-ingest) keep working
once bcolz is gone.
"""

import glob
import os

import numpy as np
import pandas as pd

from zipline.data._parquet import FIELDS
from zipline.data.bcolz_daily_bars import BcolzDailyBarReader
from zipline.data.bcolz_minute_bars import BcolzMinuteBarReader
from zipline.data.parquet_daily_bars import ParquetDailyBarWriter
from zipline.data.parquet_minute_bars import ParquetMinuteBarWriter


def convert_daily_bars(src, dest, show_progress=False):
    """Rewrite the bcolz daily bars in ``src`` as a Parquet dataset in ``dest``.

    Each asset keeps its first and last session. Prices are the values the
    bcolz reader returns, i.e. rounded to three decimal places.
    """
    reader = BcolzDailyBarReader(src)
    sessions = reader.sessions
    sids = sorted(reader._first_rows)

    def frames():
        for sid in sids:
            n_rows = reader._last_rows[sid] - reader._first_rows[sid] + 1
            if n_rows <= 0:
                yield sid, pd.DataFrame(columns=list(FIELDS), dtype="float64")
                continue
            offset = reader._calendar_offsets[sid]
            asset_sessions = sessions[offset : offset + n_rows]
            arrays = reader.load_raw_arrays(
                FIELDS, asset_sessions[0], asset_sessions[-1], [sid]
            )
            yield sid, _frame(arrays, asset_sessions)

    ParquetDailyBarWriter(
        dest, reader.trading_calendar, sessions[0], sessions[-1]
    ).write(
        frames(),
        assets=sids,
        show_progress=show_progress,
        currency_codes=pd.Series(reader.currency_codes(sids), index=sids),
    )


def convert_minute_bars(src, dest, show_progress=False):
    """Rewrite the bcolz minute bars in ``src`` as a Parquet dataset in ``dest``.

    Prices are the values the bcolz reader returns, i.e. scaled back from the
    stored integers.
    """
    reader = BcolzMinuteBarReader(src)
    calendar = reader.trading_calendar
    start_session = reader.first_trading_day
    end_session = calendar.minute_to_session(reader.last_available_dt)
    minutes = calendar.minutes_in_range(
        calendar.session_first_minute(start_session), reader.last_available_dt
    )

    def frames():
        for sid in _bcolz_minute_sids(src):
            arrays = reader.load_raw_arrays(FIELDS, minutes[0], minutes[-1], [sid])
            # The Parquet writer only stores minutes with a bar.
            yield sid, _frame(arrays, minutes)

    ParquetMinuteBarWriter(dest, calendar, start_session, end_session).write(
        frames(), show_progress=show_progress
    )


def _frame(arrays, index):
    """A frame of one asset's bars from load_raw_arrays output."""
    return pd.DataFrame(
        {
            field: np.asarray(array[:, 0], dtype="float64")
            for field, array in zip(FIELDS, arrays)
        },
        index=index,
    )


def _bcolz_minute_sids(rootdir):
    """The sids with a table in a bcolz minute dataset, in ascending order.

    Tables live at ``XX/XX/XXXXXX.bcolz`` below ``rootdir``.
    """
    paths = glob.glob(os.path.join(rootdir, "*", "*", "*.bcolz"))
    return sorted(int(os.path.basename(path)[: -len(".bcolz")]) for path in paths)
