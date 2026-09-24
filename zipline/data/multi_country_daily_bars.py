"""Daily bars for several countries."""

from functools import reduce

import numpy as np
import pandas as pd

from zipline.data.bar_reader import NoDataForSid
from zipline.data.session_bars import CurrencyAwareSessionBarReader


class MultiCountryDailyBarReader(CurrencyAwareSessionBarReader):
    """Read daily bars for several countries, each from its own reader.

    Parameters
    ----------
    readers : dict[str -> CurrencyAwareSessionBarReader]
        A dict mapping country codes to the reader of each country's bars.
        Readers must provide ``sids``, the assets they have bars for.
    """

    def __init__(self, readers):
        self._readers = readers
        self._country_map = pd.concat(
            [
                pd.Series(index=reader.sids, data=country_code)
                for country_code, reader in readers.items()
            ]
        )

    @property
    def countries(self):
        """A set-like object of the country codes supplied by this reader."""
        return self._readers.keys()

    def _country_code_for_assets(self, assets):
        # Unknown assets map to NaN.
        # Assets hash like their sids but don't match an integer index.
        country_codes = self._country_map.reindex([int(asset) for asset in assets])
        unique_country_codes = country_codes.dropna().unique()
        num_countries = len(unique_country_codes)

        if num_countries == 0:
            raise ValueError("At least one valid asset id is required.")
        elif num_countries > 1:
            raise NotImplementedError(
                "Assets were requested from multiple countries "
                f"({list(unique_country_codes)}),"
                " but multi-country reads are not yet supported."
            )

        return unique_country_codes.item()

    def load_raw_arrays(self, columns, start_date, end_date, assets):
        """
        Parameters
        ----------
        columns : list of str
           'open', 'high', 'low', 'close', or 'volume'
        start_date: Timestamp
           Beginning of the window range.
        end_date: Timestamp
           End of the window range.
        assets : list of int
           The asset identifiers in the window.

        Returns
        -------
        list of np.ndarray
            A list with an entry per field of ndarrays with shape
            (minutes in range, sids) with a dtype of float64, containing the
            values for the respective field over start and end dt range.
        """
        country_code = self._country_code_for_assets(assets)

        return self._readers[country_code].load_raw_arrays(
            columns,
            start_date,
            end_date,
            assets,
        )

    @property
    def last_available_dt(self):
        """
        Returns
        -------
        dt : pd.Timestamp
            The last session for which the reader can provide data.
        """
        return max(reader.last_available_dt for reader in self._readers.values())

    @property
    def trading_calendar(self):
        """
        Returns the zipline.utils.calendar.trading_calendar used to read
        the data.  Can be None (if the writer didn't specify it).
        """
        raise NotImplementedError(
            "Each country's bars may follow a different calendar."
        )

    @property
    def first_trading_day(self):
        """
        Returns
        -------
        dt : pd.Timestamp
            The first trading day (session) for which the reader can provide
            data.
        """
        return min(reader.first_trading_day for reader in self._readers.values())

    @property
    def sessions(self):
        """
        Returns
        -------
        sessions : DatetimeIndex
           All session labels (unioning the range for all assets) which the
           reader can provide.
        """
        return pd.DatetimeIndex(
            reduce(
                np.union1d,
                (reader.sessions for reader in self._readers.values()),
            ),
        )

    def get_value(self, sid, dt, field):
        """
        Retrieve the value at the given coordinates.

        Parameters
        ----------
        sid : int
            The asset identifier.
        dt : pd.Timestamp
            The timestamp for the desired data point.
        field : string
            The OHLVC name for the desired data point.

        Returns
        -------
        value : float|int
            The value at the given coordinates, ``float`` for OHLC, ``int``
            for 'volume'.

        Raises
        ------
        NoDataOnDate
            If the given dt is not a valid market minute (in minute mode) or
            session (in daily mode) according to this reader's tradingcalendar.
        NoDataForSid
            If the given sid is not valid.
        """
        try:
            country_code = self._country_code_for_assets([sid])
        except ValueError as err:
            raise NoDataForSid(
                f"Asset not contained in daily pricing file: {sid}"
            ) from err
        return self._readers[country_code].get_value(sid, dt, field)

    def get_last_traded_dt(self, asset, dt):
        """
        Get the latest day on or before ``dt`` in which ``asset`` traded.

        If there are no trades on or before ``dt``, returns ``pd.NaT``.

        Parameters
        ----------
        asset : zipline.asset.Asset
            The asset for which to get the last traded day.
        dt : pd.Timestamp
            The dt at which to start searching for the last traded day.

        Returns
        -------
        last_traded : pd.Timestamp
            The day of the last trade for the given asset, using the
            input dt as a vantage point.
        """
        country_code = self._country_code_for_assets([int(asset)])
        return self._readers[country_code].get_last_traded_dt(asset, dt)

    def currency_codes(self, sids):
        """Get currencies in which prices are quoted for the requested sids.

        Assumes that a sid's prices are always quoted in a single currency.

        Parameters
        ----------
        sids : np.array[int64]
            Array of sids for which currencies are needed.

        Returns
        -------
        currency_codes : np.array[S3]
            Array of currency codes for listing currencies of ``sids``.
        """
        country_code = self._country_code_for_assets(sids)
        return self._readers[country_code].currency_codes(sids)
