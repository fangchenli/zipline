#
# Copyright 2015 Quantopian, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The clock that drives a simulation."""

import numpy as np
import pandas as pd

NANOS_IN_MINUTE = 60_000_000_000

# The events the clock emits.
BAR = 0
SESSION_START = 1
SESSION_END = 2
MINUTE_END = 3
BEFORE_TRADING_START_BAR = 4


def _as_nanos(dts):
    """Convert datetime-likes to int64 nanoseconds since the epoch (UTC).

    pandas may store datetimes at resolutions other than nanoseconds, so we
    normalize the unit explicitly rather than reinterpreting raw values.
    """
    return pd.DatetimeIndex(dts).to_numpy(dtype="datetime64[ns]").view("int64")


class MinuteSimulationClock:
    """Emits the events of a simulation, session by session.

    For each session: ``(session, SESSION_START)``, then ``(minute, BAR)``
    for each of its minutes, followed by ``(minute, MINUTE_END)`` if
    ``minute_emission``, with ``(minute, BEFORE_TRADING_START_BAR)`` before
    the first minute at or after its before-trading-start minute, and last
    ``(last minute, SESSION_END)``. Sessions are tz-naive; minutes are UTC.
    """

    def __init__(
        self,
        sessions,
        market_opens,
        market_closes,
        before_trading_start_minutes,
        minute_emission=False,
    ):
        self.minute_emission = minute_emission
        self.sessions_nanos = _as_nanos(sessions)
        self.bts_nanos = _as_nanos(before_trading_start_minutes)
        self.minutes_by_session = {
            session: pd.to_datetime(
                np.arange(market_open, market_close + NANOS_IN_MINUTE, NANOS_IN_MINUTE),
                utc=True,
            )
            for session, market_open, market_close in zip(
                self.sessions_nanos,
                _as_nanos(market_opens),
                _as_nanos(market_closes),
                strict=True,
            )
        }

    def __iter__(self):
        for session_nano, bts_nano in zip(self.sessions_nanos, self.bts_nanos):
            # Session labels are tz-naive midnight timestamps.
            yield pd.Timestamp(session_nano), SESSION_START

            bts_minute = pd.Timestamp(bts_nano, tz="UTC")
            regular_minutes = self.minutes_by_session[session_nano]
            if bts_minute > regular_minutes[-1]:
                # before_trading_start is after the last close, so don't
                # emit it.
                yield from self._get_minutes_for_list(regular_minutes)
            else:
                # Sessions can start at different minutes, so search anew.
                bts_idx = regular_minutes.searchsorted(bts_minute)
                yield from self._get_minutes_for_list(regular_minutes[:bts_idx])
                yield bts_minute, BEFORE_TRADING_START_BAR
                yield from self._get_minutes_for_list(regular_minutes[bts_idx:])

            yield regular_minutes[-1], SESSION_END

    def _get_minutes_for_list(self, minutes):
        minute_emission = self.minute_emission
        for minute in minutes:
            yield minute, BAR
            if minute_emission:
                yield minute, MINUTE_END
