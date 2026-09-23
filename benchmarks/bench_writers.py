"""Write benchmarks: the cost of ingesting bars into each storage format."""

import os
import shutil
import tempfile

from zipline.utils.calendar_utils import get_calendar

from .data import (
    BACKENDS,
    CALENDAR,
    DAILY_END,
    DAILY_START,
    MINUTE_END,
    MINUTE_START,
    daily_frames,
    minute_frames,
    write_daily,
    write_minute,
)


class WriteBars:
    params = BACKENDS
    param_names = ["backend"]
    repeat = 3
    number = 1
    timeout = 300

    def setup(self, backend):
        self.calendar = get_calendar(CALENDAR)
        # Materialize the input so only the writer is timed.
        self.daily = list(
            daily_frames(self.calendar, range(200), DAILY_START, DAILY_END)
        )
        self.minute = list(
            minute_frames(self.calendar, range(20), MINUTE_START, MINUTE_END)
        )
        self.tmpdir = tempfile.mkdtemp()

    def teardown(self, backend):
        shutil.rmtree(self.tmpdir)

    def _path(self, name):
        path = os.path.join(self.tmpdir, name)
        shutil.rmtree(path, ignore_errors=True)
        return path

    def time_write_daily(self, backend):
        write_daily(
            backend,
            self._path("daily"),
            self.calendar,
            iter(self.daily),
            DAILY_START,
            DAILY_END,
        )

    def time_write_minute(self, backend):
        write_minute(
            backend,
            self._path("minute"),
            self.calendar,
            iter(self.minute),
            MINUTE_START,
            MINUTE_END,
        )
