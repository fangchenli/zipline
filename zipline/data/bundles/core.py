import errno
import os
import shutil
import warnings
from collections import namedtuple
from contextlib import ExitStack
from types import MappingProxyType

import click
import pandas as pd
from logbook import Logger
from toolz import complement, curry, take

import zipline.utils.paths as pth
from zipline.assets import ASSET_DB_VERSION, AssetDBWriter, AssetFinder
from zipline.assets.asset_db_migrations import downgrade
from zipline.utils.cache import (
    dataframe_cache,
    working_dir,
    working_file,
)
from zipline.utils.calendar_utils import get_calendar
from zipline.utils.input_validation import ensure_timestamp, optionally
from zipline.utils.preprocess import preprocess
from zipline.utils.sqlite_utils import check_and_create_engine

from ..adjustments import SQLiteAdjustmentReader, SQLiteAdjustmentWriter
from ..parquet_daily_bars import ParquetDailyBarReader, ParquetDailyBarWriter
from ..parquet_minute_bars import ParquetMinuteBarReader, ParquetMinuteBarWriter

log = Logger(__name__)


def asset_db_path(bundle_name, timestr, environ=None, db_version=None):
    return pth.data_path(
        asset_db_relative(bundle_name, timestr, db_version),
        environ=environ,
    )


def minute_equity_path(bundle_name, timestr, environ=None):
    return pth.data_path(
        minute_equity_relative(bundle_name, timestr),
        environ=environ,
    )


def daily_equity_path(bundle_name, timestr, environ=None):
    return pth.data_path(
        daily_equity_relative(bundle_name, timestr),
        environ=environ,
    )


def adjustment_db_path(bundle_name, timestr, environ=None):
    return pth.data_path(
        adjustment_db_relative(bundle_name, timestr),
        environ=environ,
    )


def cache_path(bundle_name, environ=None):
    return pth.data_path(
        cache_relative(bundle_name),
        environ=environ,
    )


def adjustment_db_relative(bundle_name, timestr):
    return bundle_name, timestr, "adjustments.sqlite"


def cache_relative(bundle_name):
    return bundle_name, ".cache"


def daily_equity_relative(bundle_name, timestr):
    return bundle_name, timestr, "daily_equities.parquet"


def bcolz_daily_equity_relative(bundle_name, timestr):
    """Where ingestions made before zipline 2.0 stored their daily bars."""
    return bundle_name, timestr, "daily_equities.bcolz"


def minute_equity_relative(bundle_name, timestr):
    return bundle_name, timestr, "minute_equities.parquet"


def bcolz_minute_equity_relative(bundle_name, timestr):
    """Where ingestions made before zipline 2.0 stored their minute bars."""
    return bundle_name, timestr, "minute_equities.bcolz"


def asset_db_relative(bundle_name, timestr, db_version=None):
    db_version = ASSET_DB_VERSION if db_version is None else db_version

    return bundle_name, timestr, f"assets-{db_version}.sqlite"


def to_bundle_ingest_dirname(ts):
    """Convert a pandas Timestamp into the name of the directory for the
    ingestion.

    Parameters
    ----------
    ts : pandas.Timestamp
        The time of the ingestions

    Returns
    -------
    name : str
        The name of the directory for this ingestion.
    """
    return ts.isoformat().replace(":", ";")


def from_bundle_ingest_dirname(cs):
    """Read a bundle ingestion directory name into a pandas Timestamp.

    Parameters
    ----------
    cs : str
        The name of the directory.

    Returns
    -------
    ts : pandas.Timestamp
        The time when this ingestion happened.
    """
    return pd.Timestamp(cs.replace(";", ":"))


def ingestions_for_bundle(bundle, environ=None):
    return sorted(
        (
            from_bundle_ingest_dirname(ing)
            for ing in os.listdir(pth.data_path([bundle], environ))
            if not pth.hidden(ing)
        ),
        reverse=True,
    )


def _require_bcolz(bundle):
    """Check that the optional bcolz support, for ``bundle``'s ingestions made
    before zipline 2.0, is installed.
    """
    try:
        import bcolz  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"Bundle {bundle!r} was ingested by zipline before 2.0 and stores its "
            "bars with bcolz, which needs the optional bcolz support: "
            "pip install 'zipline[bcolz]'. `zipline convert -b "
            f"{bundle}` then converts it to Parquet."
        ) from e


def convert(bundle, environ=None, delete_bcolz=False, show_progress=False):
    """Convert a bundle's bcolz daily and minute bars to Parquet.

    Bundles ingested before zipline 2.0 stored their bars with bcolz. This
    rewrites them in the Parquet formats for every ingestion of ``bundle``;
    ingestions already using Parquet are left alone. Each dataset is written
    to a temporary directory first, so an interrupted conversion leaves the
    ingestion as it was.

    Parameters
    ----------
    bundle : str
        The name of the bundle.
    environ : mapping, optional
        The environment variables. Defaults to os.environ.
    delete_bcolz : bool, optional
        Delete each bcolz dataset once it has been converted.
    show_progress : bool, optional
        Show the progress of each conversion.

    Returns
    -------
    converted : list[str]
        The paths of the Parquet datasets written.
    """
    converted = []
    for ingestion in ingestions_for_bundle(bundle, environ):
        timestr = to_bundle_ingest_dirname(ingestion)
        for bcolz_relative, relative, kind in (
            (bcolz_daily_equity_relative, daily_equity_relative, "daily"),
            (bcolz_minute_equity_relative, minute_equity_relative, "minute"),
        ):
            src = pth.data_path(bcolz_relative(bundle, timestr), environ=environ)
            dest = pth.data_path(relative(bundle, timestr), environ=environ)
            if not os.path.isdir(src) or os.path.exists(dest):
                continue
            _require_bcolz(bundle)
            from ..convert_bcolz import convert_daily_bars, convert_minute_bars

            convert_bars = (
                convert_daily_bars if kind == "daily" else convert_minute_bars
            )
            log.info("Converting {} to Parquet.", src)
            tmp = dest + ".converting"
            # Left over from an interrupted conversion.
            shutil.rmtree(tmp, ignore_errors=True)
            convert_bars(src, tmp, show_progress=show_progress)
            os.replace(tmp, dest)
            if delete_bcolz:
                shutil.rmtree(src)
            converted.append(dest)
    return converted


RegisteredBundle = namedtuple(
    "RegisteredBundle",
    [
        "calendar_name",
        "start_session",
        "end_session",
        "minutes_per_day",
        "ingest",
        "create_writers",
    ],
)


class BundleData(
    namedtuple(
        "BundleData",
        "asset_finder equity_minute_bar_reader equity_daily_bar_reader "
        "adjustment_reader",
    )
):
    """The readers for an ingested bundle.

    Call :meth:`close` (or use the bundle as a context manager) to release the
    database connections held by the asset finder and adjustment reader.
    """

    __slots__ = ()

    def close(self):
        self.asset_finder.engine.dispose()
        self.adjustment_reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


BundleCore = namedtuple(
    "BundleCore",
    "bundles register unregister ingest load clean",
)


class UnknownBundle(click.ClickException, LookupError):
    """Raised if no bundle with the given name was registered."""

    exit_code = 1

    def __init__(self, name):
        super().__init__(
            f"No bundle registered with the name {name!r}",
        )
        self.name = name

    def __str__(self):
        return self.message


class BadClean(click.ClickException, ValueError):
    """Exception indicating that an invalid argument set was passed to
    ``clean``.

    Parameters
    ----------
    before, after, keep_last : any
        The bad arguments to ``clean``.

    See Also
    --------
    clean
    """

    def __init__(self, before, after, keep_last):
        super().__init__(
            "Cannot pass a combination of `before` and `after` with "
            "`keep_last`. Must pass one. "
            f"Got: before={before!r}, after={after!r}, keep_last={keep_last!r}\n",
        )

    def __str__(self):
        return self.message


def _make_bundle_core():
    """Create a family of data bundle functions that read from the same
    bundle mapping.

    Returns
    -------
    bundles : MappingProxyType
        The mapping of bundles to bundle payloads.
    register : callable
        The function which registers new bundles in the ``bundles`` mapping.
    unregister : callable
        The function which deregisters bundles from the ``bundles`` mapping.
    ingest : callable
        The function which downloads and write data for a given data bundle.
    load : callable
        The function which loads the ingested bundles back into memory.
    clean : callable
        The function which cleans up data written with ``ingest``.
    """
    _bundles = {}  # the registered bundles
    # Expose _bundles through a proxy so that users cannot mutate this
    # accidentally. Users may go through `register` to update this which will
    # warn when trampling another bundle.
    bundles = MappingProxyType(_bundles)

    @curry
    def register(
        name,
        f,
        calendar_name="NYSE",
        start_session=None,
        end_session=None,
        minutes_per_day=None,
        create_writers=True,
    ):
        """Register a data bundle ingest function.

        Parameters
        ----------
        name : str
            The name of the bundle.
        f : callable
            The ingest function. This function will be passed:

              environ : mapping
                  The environment this is being run with.
              asset_db_writer : AssetDBWriter
                  The asset db writer to write into.
              minute_bar_writer : ParquetMinuteBarWriter
                  The minute bar writer to write into.
              daily_bar_writer : ParquetDailyBarWriter
                  The daily bar writer to write into.
              adjustment_writer : SQLiteAdjustmentWriter
                  The adjustment db writer to write into.
              calendar : zipline.utils.calendar_utils.ExchangeCalendar
                  The trading calendar to ingest for.
              start_session : pd.Timestamp
                  The first session of data to ingest.
              end_session : pd.Timestamp
                  The last session of data to ingest.
              cache : DataFrameCache
                  A mapping object to temporarily store dataframes.
                  This should be used to cache intermediates in case the load
                  fails. This will be automatically cleaned up after a
                  successful load.
              show_progress : bool
                  Show the progress for the current load where possible.
        calendar_name : str, optional
            The name of a calendar used to align bundle data.
            Default is 'NYSE'.
        start_session : pd.Timestamp, optional
            The first session for which we want data. If not provided,
            or if the date lies outside the range supported by the
            calendar, the first_session of the calendar is used.
        end_session : pd.Timestamp, optional
            The last session for which we want data. If not provided,
            or if the date lies outside the range supported by the
            calendar, the last_session of the calendar is used.
        minutes_per_day : int, optional
            Deprecated and ignored. Minute bars are stored at the calendar's
            trading minutes, so the number of minutes per day is not needed.
        create_writers : bool, optional
            Should the ingest machinery create the writers for the ingest
            function. This can be disabled as an optimization for cases where
            they are not needed, e.g. for a bundle that downloads pre-built data.

        Notes
        -----
        This function my be used as a decorator, for example:

        .. code-block:: python

           @register('quandl')
           def quandl_ingest_function(...):
               ...

        See Also
        --------
        zipline.data.bundles.bundles
        """
        if name in bundles:
            warnings.warn(
                f"Overwriting bundle with name {name!r}",
                stacklevel=3,
            )
        if minutes_per_day is not None:
            warnings.warn(
                "register(minutes_per_day=...) is deprecated and ignored: minute "
                "bars are stored at the calendar's trading minutes",
                DeprecationWarning,
                stacklevel=3,
            )

        # NOTE: We don't eagerly compute calendar values here because
        # `register` is called at module scope in zipline, and creating a
        # calendar currently takes between 0.5 and 1 seconds, which causes a
        # noticeable delay on the zipline CLI.
        _bundles[name] = RegisteredBundle(
            calendar_name=calendar_name,
            start_session=start_session,
            end_session=end_session,
            minutes_per_day=minutes_per_day,
            ingest=f,
            create_writers=create_writers,
        )
        return f

    def unregister(name):
        """Unregister a bundle.

        Parameters
        ----------
        name : str
            The name of the bundle to unregister.

        Raises
        ------
        UnknownBundle
            Raised when no bundle has been registered with the given name.

        See Also
        --------
        zipline.data.bundles.bundles
        """
        try:
            del _bundles[name]
        except KeyError:
            raise UnknownBundle(name) from None

    def ingest(
        name,
        environ=os.environ,
        timestamp=None,
        assets_versions=(),
        show_progress=False,
    ):
        """Ingest data for a given bundle.

        Parameters
        ----------
        name : str
            The name of the bundle.
        environ : mapping, optional
            The environment variables. By default this is os.environ.
        timestamp : datetime, optional
            The timestamp to use for the load.
            By default this is the current time.
        assets_versions : Iterable[int], optional
            Versions of the assets db to which to downgrade.
        show_progress : bool, optional
            Tell the ingest function to display the progress where possible.
        """
        try:
            bundle = bundles[name]
        except KeyError:
            raise UnknownBundle(name) from None

        calendar = get_calendar(bundle.calendar_name)

        start_session = bundle.start_session
        end_session = bundle.end_session

        if start_session is None or start_session < calendar.first_session:
            start_session = calendar.first_session

        if end_session is None or end_session > calendar.last_session:
            end_session = calendar.last_session

        if timestamp is None:
            timestamp = pd.Timestamp.now("UTC")
        timestamp = timestamp.tz_convert("utc").tz_localize(None)

        timestr = to_bundle_ingest_dirname(timestamp)
        cachepath = cache_path(name, environ=environ)
        pth.ensure_directory(pth.data_path([name, timestr], environ=environ))
        pth.ensure_directory(cachepath)
        with (
            dataframe_cache(cachepath, clean_on_failure=False) as cache,
            ExitStack() as stack,
        ):
            # we use `cleanup_on_failure=False` so that we don't purge the
            # cache directory if the load fails in the middle
            if bundle.create_writers:
                wd = stack.enter_context(
                    working_dir(pth.data_path([], environ=environ))
                )
                daily_bars_path = wd.ensure_dir(*daily_equity_relative(name, timestr))
                daily_bar_writer = ParquetDailyBarWriter(
                    daily_bars_path,
                    calendar,
                    start_session,
                    end_session,
                )
                minute_bar_writer = ParquetMinuteBarWriter(
                    wd.ensure_dir(*minute_equity_relative(name, timestr)),
                    calendar,
                    start_session,
                    end_session,
                )
                assets_db_path = wd.getpath(*asset_db_relative(name, timestr))
                asset_db_writer = AssetDBWriter(assets_db_path)
                stack.callback(asset_db_writer.engine.dispose)

                adjustment_db_writer = stack.enter_context(
                    SQLiteAdjustmentWriter(
                        wd.getpath(*adjustment_db_relative(name, timestr)),
                        # Opens lazily, after the bundle has written its
                        # daily bars, to compute dividend ratios.
                        ParquetDailyBarReader(daily_bars_path),
                        overwrite=True,
                    )
                )
            else:
                daily_bar_writer = None
                minute_bar_writer = None
                asset_db_writer = None
                adjustment_db_writer = None
                if assets_versions:
                    raise ValueError(
                        "Need to ingest a bundle that creates "
                        "writers in order to downgrade the assets"
                        " db."
                    )
            log.info("Ingesting {}.", name)
            bundle.ingest(
                environ,
                asset_db_writer,
                minute_bar_writer,
                daily_bar_writer,
                adjustment_db_writer,
                calendar,
                start_session,
                end_session,
                cache,
                show_progress,
                pth.data_path([name, timestr], environ=environ),
            )
            # Write an empty dataset for each kind of bars the bundle didn't
            # provide, so that the bundle can still be loaded.
            for writer in (daily_bar_writer, minute_bar_writer):
                if writer is not None and not writer.exists:
                    writer.write(())

            for version in sorted(set(assets_versions), reverse=True):
                version_path = wd.getpath(
                    *asset_db_relative(
                        name,
                        timestr,
                        db_version=version,
                    )
                )
                with working_file(version_path) as wf:
                    shutil.copy2(assets_db_path, wf.path)
                    engine = check_and_create_engine(wf.path, require_exists=True)
                    try:
                        downgrade(engine, version)
                    finally:
                        engine.dispose()

    def most_recent_data(bundle_name, timestamp, environ=None):
        """Get the path to the most recent data after ``date``for the
        given bundle.

        Parameters
        ----------
        bundle_name : str
            The name of the bundle to lookup.
        timestamp : datetime
            The timestamp to begin searching on or before.
        environ : dict, optional
            An environment dict to forward to zipline_root.
        """
        if bundle_name not in bundles:
            raise UnknownBundle(bundle_name)

        try:
            candidates = os.listdir(
                pth.data_path([bundle_name], environ=environ),
            )
            return pth.data_path(
                [
                    bundle_name,
                    max(
                        filter(complement(pth.hidden), candidates),
                        key=from_bundle_ingest_dirname,
                    ),
                ],
                environ=environ,
            )
        except (ValueError, OSError) as e:
            if getattr(e, "errno", errno.ENOENT) != errno.ENOENT:
                raise
            raise ValueError(
                f"no data for bundle {bundle_name!r} on or before {timestamp}\n"
                f"maybe you need to run: $ zipline ingest -b {bundle_name}",
            ) from e

    def load(name, environ=os.environ, timestamp=None):
        """Loads a previously ingested bundle.

        Parameters
        ----------
        name : str
            The name of the bundle.
        environ : mapping, optional
            The environment variables. Defaults of os.environ.
        timestamp : datetime, optional
            The timestamp of the data to lookup.
            Defaults to the current time.

        Returns
        -------
        bundle_data : BundleData
            The raw data readers for this bundle.
        """
        if timestamp is None:
            timestamp = pd.Timestamp.now("UTC")
        timestr = most_recent_data(name, timestamp, environ=environ)
        daily_path = daily_equity_path(name, timestr, environ=environ)
        minute_path = minute_equity_path(name, timestr, environ=environ)
        if not (os.path.exists(daily_path) and os.path.exists(minute_path)):
            # Ingested before zipline 2.0, with bars stored by bcolz.
            _require_bcolz(name)
            warnings.warn(
                f"Bundle {name!r} stores its bars with bcolz, which is deprecated "
                "and will not be supported in a future version of zipline. Run "
                f"`zipline convert -b {name}` to convert it to Parquet.",
                FutureWarning,
                stacklevel=2,
            )
        if os.path.exists(daily_path):
            daily_bar_reader = ParquetDailyBarReader(daily_path)
        else:
            from ..bcolz_daily_bars import BcolzDailyBarReader

            daily_bar_reader = BcolzDailyBarReader(
                pth.data_path(
                    bcolz_daily_equity_relative(name, timestr), environ=environ
                )
            )
        if os.path.exists(minute_path):
            minute_bar_reader = ParquetMinuteBarReader(minute_path)
        else:
            from ..bcolz_minute_bars import BcolzMinuteBarReader

            minute_bar_reader = BcolzMinuteBarReader(
                pth.data_path(
                    bcolz_minute_equity_relative(name, timestr), environ=environ
                )
            )
        return BundleData(
            asset_finder=AssetFinder(
                asset_db_path(name, timestr, environ=environ),
            ),
            equity_minute_bar_reader=minute_bar_reader,
            equity_daily_bar_reader=daily_bar_reader,
            adjustment_reader=SQLiteAdjustmentReader(
                adjustment_db_path(name, timestr, environ=environ),
            ),
        )

    @preprocess(
        before=optionally(ensure_timestamp),
        after=optionally(ensure_timestamp),
    )
    def clean(name, before=None, after=None, keep_last=None, environ=os.environ):
        """Clean up data that was created with ``ingest`` or
        ``$ python -m zipline ingest``

        Parameters
        ----------
        name : str
            The name of the bundle to remove data for.
        before : datetime, optional
            Remove data ingested before this date.
            This argument is mutually exclusive with: keep_last
        after : datetime, optional
            Remove data ingested after this date.
            This argument is mutually exclusive with: keep_last
        keep_last : int, optional
            Remove all but the last ``keep_last`` ingestions.
            This argument is mutually exclusive with:
              before
              after
        environ : mapping, optional
            The environment variables. Defaults of os.environ.

        Returns
        -------
        cleaned : set[str]
            The names of the runs that were removed.

        Raises
        ------
        BadClean
            Raised when ``before`` and or ``after`` are passed with
            ``keep_last``. This is a subclass of ``ValueError``.
        """
        try:
            all_runs = sorted(
                filter(
                    complement(pth.hidden),
                    os.listdir(pth.data_path([name], environ=environ)),
                ),
                key=from_bundle_ingest_dirname,
            )
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            raise UnknownBundle(name) from e

        if before is after is keep_last is None:
            raise BadClean(before, after, keep_last)
        if (before is not None or after is not None) and keep_last is not None:
            raise BadClean(before, after, keep_last)

        if keep_last is None:

            def should_clean(name):
                dt = from_bundle_ingest_dirname(name)
                return (before is not None and dt < before) or (
                    after is not None and dt > after
                )

        elif keep_last >= 0:
            last_n_dts = set(take(keep_last, reversed(all_runs)))

            def should_clean(name):
                return name not in last_n_dts
        else:
            raise BadClean(before, after, keep_last)

        cleaned = set()
        for run in all_runs:
            if should_clean(run):
                log.info("Cleaning {}.", run)
                path = pth.data_path([name, run], environ=environ)
                shutil.rmtree(path)
                cleaned.add(path)

        return cleaned

    return BundleCore(bundles, register, unregister, ingest, load, clean)


bundles, register, unregister, ingest, load, clean = _make_bundle_core()
