"""Storage-level benchmarks: the bar reader interface used by the DataPortal
and the Pipeline loaders.
"""

from .common import BundleBenchmark, random_choices
from .data import DAILY_BACKENDS, DAILY_SIDS, MINUTE_BACKENDS, MINUTE_SIDS

FIELDS = ["open", "high", "low", "close", "volume"]
N_LOOKUPS = 1000


class DailyBarReader(BundleBenchmark):
    params = (DAILY_BACKENDS, [1, 100, DAILY_SIDS], [20, 252, 2500])
    param_names = ["backend", "n_sids", "n_sessions"]

    def setup(self, roots, backend, n_sids, n_sessions):
        self.reader = self.bundle(roots, backend).daily_reader()
        sessions = self.reader.sessions
        self.sids = list(range(n_sids))
        self.start, self.end = sessions[-n_sessions], sessions[-1]

    def time_load_raw_arrays(self, roots, backend, n_sids, n_sessions):
        self.reader.load_raw_arrays(FIELDS, self.start, self.end, self.sids)

    def peakmem_load_raw_arrays(self, roots, backend, n_sids, n_sessions):
        self.reader.load_raw_arrays(FIELDS, self.start, self.end, self.sids)


class DailyBarLookups(BundleBenchmark):
    params = DAILY_BACKENDS
    param_names = ["backend"]

    def setup(self, roots, backend):
        bundle = self.bundle(roots, backend)
        self.reader = bundle.daily_reader()
        sessions = self.reader.sessions
        self.lookups = list(
            zip(
                random_choices(range(DAILY_SIDS), N_LOOKUPS, seed=1),
                random_choices(sessions, N_LOOKUPS, seed=2),
            )
        )
        self.assets = bundle.asset_finder().retrieve_all(sid for sid, _ in self.lookups)

    def time_get_value(self, roots, backend):
        get_value = self.reader.get_value
        for sid, session in self.lookups:
            get_value(sid, session, "close")

    def time_get_last_traded_dt(self, roots, backend):
        get_last_traded_dt = self.reader.get_last_traded_dt
        for asset, (_, session) in zip(self.assets, self.lookups):
            get_last_traded_dt(asset, session)

    def time_open(self, roots, backend):
        # Opening a reader includes reading its metadata and session index.
        return self.bundle(roots, backend).daily_reader().sessions


class MinuteBarReader(BundleBenchmark):
    params = (MINUTE_BACKENDS, [1, MINUTE_SIDS], [390, 5 * 390, 20 * 390])
    param_names = ["backend", "n_sids", "n_minutes"]

    def setup(self, roots, backend, n_sids, n_minutes):
        bundle = self.bundle(roots, backend)
        self.reader = bundle.minute_reader()
        minutes = bundle.minutes()
        self.sids = list(range(n_sids))
        self.start, self.end = minutes[-n_minutes], minutes[-1]

    def time_load_raw_arrays(self, roots, backend, n_sids, n_minutes):
        self.reader.load_raw_arrays(FIELDS, self.start, self.end, self.sids)

    def peakmem_load_raw_arrays(self, roots, backend, n_sids, n_minutes):
        self.reader.load_raw_arrays(FIELDS, self.start, self.end, self.sids)


class MinuteBarLookups(BundleBenchmark):
    params = MINUTE_BACKENDS
    param_names = ["backend"]

    def setup(self, roots, backend):
        bundle = self.bundle(roots, backend)
        self.reader = bundle.minute_reader()
        minutes = bundle.minutes()
        self.lookups = list(
            zip(
                random_choices(range(MINUTE_SIDS), N_LOOKUPS, seed=1),
                random_choices(minutes, N_LOOKUPS, seed=2),
            )
        )
        self.assets = bundle.asset_finder().retrieve_all(sid for sid, _ in self.lookups)

    def time_get_value(self, roots, backend):
        get_value = self.reader.get_value
        for sid, minute in self.lookups:
            get_value(sid, minute, "close")

    def time_get_last_traded_dt(self, roots, backend):
        get_last_traded_dt = self.reader.get_last_traded_dt
        for asset, (_, minute) in zip(self.assets, self.lookups):
            get_last_traded_dt(asset, minute)
