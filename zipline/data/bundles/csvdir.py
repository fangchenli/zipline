"""
Module for building a complete dataset from local directory with csv files.
"""

import logging
import os

import pandas as pd

from zipline.utils.calendar_utils import register_calendar_alias
from zipline.utils.cli import maybe_show_progress

from . import core as bundles

logger = logging.getLogger(__name__)


def csvdir_equities(tframes=None, csvdir=None):
    """
    Generate an ingest function for custom data bundle
    This function can be used in ~/.zipline/extension.py
    to register bundle with custom parameters, e.g. with
    a custom trading calendar.

    Parameters
    ----------
    tframes: tuple, optional
        The data time frames, supported timeframes: 'daily' and 'minute'
    csvdir : string, optional, default: CSVDIR environment variable
        The path to the directory of this structure:
        <directory>/<timeframe1>/<symbol1>.csv
        <directory>/<timeframe1>/<symbol2>.csv
        <directory>/<timeframe1>/<symbol3>.csv
        <directory>/<timeframe2>/<symbol1>.csv
        <directory>/<timeframe2>/<symbol2>.csv
        <directory>/<timeframe2>/<symbol3>.csv

    Returns
    -------
    ingest : callable
        The bundle ingest function

    Examples
    --------
    This code should be added to ~/.zipline/extension.py
    .. code-block:: python
       from zipline.data.bundles import csvdir_equities, register
       register('custom-csvdir-bundle',
                csvdir_equities(["daily", "minute"],
                '/full/path/to/the/csvdir/directory'))
    """

    return CSVDIRBundle(tframes, csvdir).ingest


class CSVDIRBundle:
    """
    Wrapper class to call csvdir_bundle with provided
    list of time frames and a path to the csvdir directory
    """

    def __init__(self, tframes=None, csvdir=None):
        self.tframes = tframes
        self.csvdir = csvdir

    def ingest(
        self,
        environ,
        asset_db_writer,
        minute_bar_writer,
        daily_bar_writer,
        adjustment_writer,
        calendar,
        start_session,
        end_session,
        cache,
        show_progress,
        output_dir,
    ):

        csvdir_bundle(
            environ,
            asset_db_writer,
            minute_bar_writer,
            daily_bar_writer,
            adjustment_writer,
            calendar,
            start_session,
            end_session,
            cache,
            show_progress,
            output_dir,
            self.tframes,
            self.csvdir,
        )


TIMEFRAMES = ("daily", "minute")

DIVIDEND_COLUMNS = [
    "sid",
    "amount",
    "ex_date",
    "record_date",
    "declared_date",
    "pay_date",
]
SPLIT_COLUMNS = ["sid", "ratio", "effective_date"]


@bundles.register("csvdir")
def csvdir_bundle(
    environ,
    asset_db_writer,
    minute_bar_writer,
    daily_bar_writer,
    adjustment_writer,
    calendar,
    start_session,
    end_session,
    cache,
    show_progress,
    output_dir,
    tframes=None,
    csvdir=None,
):
    """
    Build a zipline data bundle from the directory with csv files.

    Each symbol gets one sid, whichever time frames it has files in, and its
    lifetime spans all of its bars. Splits and dividends are read from the
    daily files, or from the minute files if there are no daily ones.
    """
    if not csvdir:
        csvdir = environ.get("CSVDIR")
        if not csvdir:
            raise ValueError("CSVDIR environment variable is not set")

    if not os.path.isdir(csvdir):
        raise ValueError(f"{csvdir} is not a directory")

    if not tframes:
        tframes = set(TIMEFRAMES).intersection(os.listdir(csvdir))

        if not tframes:
            raise ValueError(
                f"'daily' and 'minute' directories not found in '{csvdir}'"
            )
    unknown = set(tframes).difference(TIMEFRAMES)
    if unknown:
        raise ValueError(
            f"Unknown time frames {sorted(unknown)}; expected 'daily' and/or 'minute'"
        )
    # Daily bars are written first, since dividend ratios are computed from
    # them.
    tframes = [tframe for tframe in TIMEFRAMES if tframe in tframes]

    files = {tframe: _csv_files(os.path.join(csvdir, tframe)) for tframe in tframes}
    symbols = sorted(set().union(*files.values()))
    sids = {symbol: sid for sid, symbol in enumerate(symbols)}

    # sid -> (first session, last session)
    lifetimes = {}
    splits = []
    dividends = []
    for tframe in tframes:
        writer = daily_bar_writer if tframe == "daily" else minute_bar_writer
        writer.write(
            _pricing_iter(
                tframe,
                os.path.join(csvdir, tframe),
                files[tframe],
                sids,
                calendar,
                lifetimes,
                # Only read adjustments from one time frame, so they aren't
                # counted twice.
                (splits, dividends) if tframe == tframes[0] else None,
                show_progress,
            ),
            show_progress=show_progress,
        )

    metadata = pd.DataFrame(
        {
            "start_date": [lifetimes[sid][0] for sid in range(len(symbols))],
            "end_date": [lifetimes[sid][1] for sid in range(len(symbols))],
            "symbol": symbols,
            # Hardcode the exchange to "CSVDIR" for all assets and (elsewhere)
            # register "CSVDIR" to resolve to the NYSE calendar, because these
            # are all equities and thus can use the NYSE calendar.
            "exchange": "CSVDIR",
        }
    )
    # The auto_close date is the day after the last trade.
    metadata["auto_close_date"] = metadata["end_date"] + pd.Timedelta(days=1)
    asset_db_writer.write(equities=metadata)

    adjustment_writer.write(
        splits=_concat(splits, SPLIT_COLUMNS),
        dividends=_concat(dividends, DIVIDEND_COLUMNS),
    )


def _csv_files(directory):
    """Map each symbol to its file, named ``<symbol>.csv`` or e.g.
    ``<symbol>.csv.gz``.
    """
    files = {}
    for name in os.listdir(directory):
        symbol, csv, _ = name.partition(".csv")
        if csv:
            files[symbol] = name
    if not files:
        raise ValueError(f"no <symbol>.csv* files found in {directory}")
    return files


def _pricing_iter(
    tframe, directory, files, sids, calendar, lifetimes, adjustments, show_progress
):
    with maybe_show_progress(
        sorted(files),
        show_progress,
        label=f"Loading custom {tframe} pricing data: ",
    ) as it:
        for symbol in it:
            sid = sids[symbol]
            logger.info("%s: sid %s", symbol, sid)

            dfr = pd.read_csv(
                os.path.join(directory, files[symbol]), parse_dates=[0], index_col=0
            ).sort_index()
            index = dfr.index = pd.DatetimeIndex(dfr.index).as_unit("ns")

            if tframe == "daily":
                sessions = index
            else:
                minutes = (
                    index.tz_localize("UTC")
                    if index.tz is None
                    else index.tz_convert("UTC")
                )
                sessions = calendar.minutes_to_sessions(minutes)

            first, last = sessions[0], sessions[-1]
            if sid in lifetimes:
                first = min(first, lifetimes[sid][0])
                last = max(last, lifetimes[sid][1])
            lifetimes[sid] = first, last

            if adjustments is not None:
                splits, dividends = adjustments
                if "split" in dfr.columns:
                    is_split = (dfr["split"] != 1.0).to_numpy()
                    splits.append(
                        pd.DataFrame(
                            {
                                "sid": sid,
                                "ratio": 1.0 / dfr["split"].to_numpy()[is_split],
                                "effective_date": sessions[is_split],
                            }
                        )
                    )
                if "dividend" in dfr.columns:
                    is_dividend = (dfr["dividend"] != 0.0).to_numpy()
                    dividends.append(
                        pd.DataFrame(
                            {
                                "sid": sid,
                                "amount": dfr["dividend"].to_numpy()[is_dividend],
                                "ex_date": sessions[is_dividend],
                                "record_date": pd.NaT,
                                "declared_date": pd.NaT,
                                "pay_date": pd.NaT,
                            }
                        )
                    )

            yield sid, dfr


def _concat(frames, columns):
    """The adjustments read from each file, or None if there are none."""
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)[columns]


register_calendar_alias("CSVDIR", "NYSE")
