"""Pipeline benchmarks: computing common factors over the daily bundle."""

from zipline.pipeline import Pipeline, SimplePipelineEngine
from zipline.pipeline.data import EquityPricing
from zipline.pipeline.domain import US_EQUITIES
from zipline.pipeline.factors import (
    AverageDollarVolume,
    Returns,
    SimpleMovingAverage,
)
from zipline.pipeline.loaders import EquityPricingLoader

from .common import BundleBenchmark
from .data import DAILY_BACKENDS


def make_pipeline():
    returns = Returns(window_length=21)
    fast = SimpleMovingAverage(inputs=[EquityPricing.close], window_length=50)
    slow = SimpleMovingAverage(inputs=[EquityPricing.close], window_length=200)
    liquid = AverageDollarVolume(window_length=30).top(200)
    return Pipeline(
        columns={
            "returns": returns,
            "momentum_rank": returns.rank(mask=liquid),
            "trend": fast > slow,
        },
        screen=liquid,
        domain=US_EQUITIES,
    )


class RunPipeline(BundleBenchmark):
    params = (DAILY_BACKENDS, [21, 252])
    param_names = ["backend", "n_sessions"]

    def setup(self, roots, backend, n_sessions):
        bundle = self.bundle(roots, backend)
        loader = EquityPricingLoader.without_fx(
            bundle.daily_reader(), bundle.adjustment_reader()
        )
        self.engine = SimplePipelineEngine(
            lambda column: loader, bundle.asset_finder(), default_domain=US_EQUITIES
        )
        sessions = bundle.daily_reader().sessions
        self.start, self.end = sessions[-n_sessions], sessions[-1]
        self.pipeline = make_pipeline()

    def time_run_pipeline(self, roots, backend, n_sessions):
        self.engine.run_pipeline(self.pipeline, self.start, self.end)

    def peakmem_run_pipeline(self, roots, backend, n_sessions):
        self.engine.run_pipeline(self.pipeline, self.start, self.end)

    def time_run_chunked_pipeline(self, roots, backend, n_sessions):
        self.engine.run_chunked_pipeline(
            self.pipeline, self.start, self.end, chunksize=21
        )
