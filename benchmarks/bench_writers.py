"""Write benchmarks: the cost of ingesting bars into each storage format."""

import os
import shutil
import tempfile

from zipline.utils.calendar_utils import get_calendar

from .data import (
    CALENDAR,
    DAILY_BACKENDS,
    DAILY_END,
    DAILY_START,
    MINUTE_BACKENDS,
    MINUTE_END,
    MINUTE_START,
    daily_frames,
    minute_frames,
    write_daily,
    write_minute,
)


class _WriteBenchmark:
    repeat = 3
    number = 1
    timeout = 300

    def setup(self, backend):
        self.calendar = get_calendar(CALENDAR)
        self.tmpdir = tempfile.mkdtemp()
        self.materialize()

    def teardown(self, backend):
        shutil.rmtree(self.tmpdir)

    def fresh_path(self, name):
        path = os.path.join(self.tmpdir, name)
        shutil.rmtree(path, ignore_errors=True)
        return path


class WriteDailyBars(_WriteBenchmark):
    params = DAILY_BACKENDS
    param_names = ["backend"]

    def materialize(self):
        # Materialize the input so only the writer is timed.
        self.frames = list(
            daily_frames(self.calendar, range(200), DAILY_START, DAILY_END)
        )

    def time_write_daily(self, backend):
        write_daily(
            backend,
            self.fresh_path("daily"),
            self.calendar,
            iter(self.frames),
            DAILY_START,
            DAILY_END,
        )


class WriteMinuteBars(_WriteBenchmark):
    params = MINUTE_BACKENDS
    param_names = ["backend"]

    def materialize(self):
        self.frames = list(
            minute_frames(self.calendar, range(20), MINUTE_START, MINUTE_END)
        )

    def time_write_minute(self, backend):
        write_minute(
            backend,
            self.fresh_path("minute"),
            self.calendar,
            iter(self.frames),
            MINUTE_START,
            MINUTE_END,
        )
