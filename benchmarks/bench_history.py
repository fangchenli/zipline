"""DataPortal benchmarks: what ``data.history`` and ``data.current`` cost an
algorithm, including adjustments and the history window caches.
"""

from .common import BundleBenchmark
from .data import DAILY_BACKENDS, MINUTE_BACKENDS

# Consecutive bars requested one after another, as a running algorithm does,
# so the sliding-window caches are exercised.
N_STEPS = 50


class DailyHistory(BundleBenchmark):
    params = (DAILY_BACKENDS, [1, 100], [20, 252])
    param_names = ["backend", "n_assets", "bar_count"]

    def setup(self, roots, backend, n_assets, bar_count):
        bundle = self.bundle(roots, backend)
        self.portal = bundle.data_portal()
        self.assets = bundle.asset_finder().retrieve_all(range(n_assets))
        # The last minute of each of the last N_STEPS sessions.
        sessions = bundle.daily_reader().sessions[-N_STEPS:]
        self.minutes = [bundle.calendar.session_last_minute(s) for s in sessions]

    def time_get_history_window(self, roots, backend, n_assets, bar_count):
        for dt in self.minutes:
            self.portal.get_history_window(
                self.assets, dt, bar_count, "1d", "close", "daily"
            )


class MinuteHistory(BundleBenchmark):
    params = (MINUTE_BACKENDS, [1, 50], [30, 390])
    param_names = ["backend", "n_assets", "bar_count"]

    def setup(self, roots, backend, n_assets, bar_count):
        bundle = self.bundle(roots, backend)
        self.portal = bundle.data_portal(minute=True)
        self.assets = bundle.asset_finder().retrieve_all(range(n_assets))
        self.minutes = bundle.minutes()[-N_STEPS:]

    def time_get_history_window(self, roots, backend, n_assets, bar_count):
        for dt in self.minutes:
            self.portal.get_history_window(
                self.assets, dt, bar_count, "1m", "close", "minute"
            )

    def time_get_spot_value(self, roots, backend, n_assets, bar_count):
        for dt in self.minutes:
            self.portal.get_spot_value(self.assets, "close", dt, "minute")
