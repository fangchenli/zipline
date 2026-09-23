"""End-to-end backtest benchmarks: the whole simulation loop (clock, blotter,
ledger, metrics) on top of the data layer.
"""

import pandas as pd

from zipline import TradingAlgorithm
from zipline.api import (
    date_rules,
    order_target_percent,
    record,
    schedule_function,
    time_rules,
)
from zipline.finance.trading import SimulationParameters

from .common import BundleBenchmark
from .data import DAILY_BACKENDS, DAILY_SIDS, MINUTE_BACKENDS, MINUTE_SIDS


def run(bundle, start, end, data_frequency, initialize, handle_data=None):
    calendar = bundle.calendar
    sim_params = SimulationParameters(
        start_session=start,
        end_session=end,
        trading_calendar=calendar,
        capital_base=10_000_000,
        data_frequency=data_frequency,
        emission_rate="daily",
    )
    sessions = calendar.sessions_in_range(start, end)
    algo = TradingAlgorithm(
        sim_params=sim_params,
        data_portal=bundle.data_portal(minute=data_frequency == "minute"),
        trading_calendar=calendar,
        benchmark_returns=pd.Series(0.0, index=sessions),
        initialize=initialize,
        handle_data=handle_data,
    )
    return algo.run()


def momentum_initialize(context):
    context.universe = context.sids
    schedule_function(
        momentum_rebalance, date_rules.month_start(), time_rules.market_open()
    )


def momentum_rebalance(context, data):
    prices = data.history(context.universe, "close", 63, "1d")
    returns = prices.iloc[-1] / prices.iloc[0] - 1
    top = returns.nlargest(20).index
    for asset in context.universe:
        weight = 1 / len(top) if asset in top else 0.0
        order_target_percent(asset, weight)
    record(n_held=len(top))


class DailyBacktest(BundleBenchmark):
    params = (DAILY_BACKENDS, [100, DAILY_SIDS])
    param_names = ["backend", "n_assets"]
    # A single backtest takes seconds; a few repeats are enough.
    repeat = 3
    number = 1

    def setup(self, roots, backend, n_assets):
        self.bundle_ = self.bundle(roots, backend)
        self.assets = self.bundle_.asset_finder().retrieve_all(range(n_assets))
        sessions = self.bundle_.daily_reader().sessions
        self.start, self.end = sessions[-252], sessions[-1]

    def time_backtest(self, roots, backend, n_assets):
        assets = self.assets

        def initialize(context):
            context.sids = assets
            momentum_initialize(context)

        run(self.bundle_, self.start, self.end, "daily", initialize)


class MinuteBacktest(BundleBenchmark):
    params = (MINUTE_BACKENDS, [10, MINUTE_SIDS])
    param_names = ["backend", "n_assets"]
    repeat = 3
    number = 1

    def setup(self, roots, backend, n_assets):
        self.bundle_ = self.bundle(roots, backend)
        self.assets = self.bundle_.asset_finder().retrieve_all(range(n_assets))
        sessions = self.bundle_.minute_sessions()
        # Five trading days of minute bars.
        self.start, self.end = sessions[-5], sessions[-1]

    def time_backtest(self, roots, backend, n_assets):
        assets = self.assets

        def initialize(context):
            context.assets = assets
            context.i = 0

        def handle_data(context, data):
            context.i += 1
            prices = data.current(context.assets, "price")
            if context.i % 30 == 0:
                for asset in context.assets:
                    order_target_percent(asset, 1 / len(context.assets))
            record(mean_price=prices.mean())

        run(self.bundle_, self.start, self.end, "minute", initialize, handle_data)
