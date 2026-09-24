"""Types that pipeline users get from Pipeline and Factor, checked by ty.

Like ``api_types.py``, this module isn't run: CI type-checks it, and each
``assert_type`` fails if an annotation changes what a pipeline author sees.
"""

from typing import assert_type

from zipline.pipeline import Classifier, CustomFactor, Factor, Filter, Pipeline
from zipline.pipeline.data import EquityPricing
from zipline.pipeline.factors import Returns, SimpleMovingAverage
from zipline.pipeline.factors.factor import RecarrayField
from zipline.pipeline.factors.statistical import RollingLinearRegression
from zipline.pipeline.term import ComputableTerm


def make_pipeline() -> Pipeline:
    returns = Returns(window_length=2)
    sma = SimpleMovingAverage(inputs=[EquityPricing.close], window_length=10)
    universe = sma.top(500)
    assert_type(universe, Filter)

    # Arithmetic gives a Factor, comparison a Filter, whatever the operands.
    assert_type(returns + sma, Factor)
    assert_type(returns * 2, Factor)
    assert_type(2 - returns, Factor)
    assert_type(-returns, Factor)
    assert_type(returns.log(), Factor)
    assert_type(returns > 0, Filter)
    assert_type(returns.eq(sma), Filter)
    assert_type((returns > 0) & universe, Filter)

    ranked = returns.rank(method="average", mask=universe)
    assert_type(ranked, Factor)
    assert_type(returns.zscore(groupby=returns.quintiles()), Factor)
    assert_type(returns.demean(mask=universe), Factor)
    assert_type(returns.winsorize(0.05, 0.95), Factor)
    assert_type(returns.clip(-1.0, 1.0), Factor)
    assert_type(returns.pearsonr(EquityPricing.close.latest, 30), Factor)
    assert_type(returns.mean(), Factor)
    assert_type(returns.quantiles(4), Classifier)
    assert_type(returns.bottom(10, groupby=returns.deciles()), Filter)
    assert_type(returns.percentile_between(10, 90), Filter)
    assert_type(returns.notnan(), Filter)

    regression = returns.linear_regression(EquityPricing.close.latest, 60)
    assert_type(regression, RollingLinearRegression)
    assert_type(regression.beta, Factor)

    pipe = Pipeline(columns={"returns": returns}, screen=universe)
    pipe.add(ranked, "ranked")
    assert_type(pipe.columns, dict[str, ComputableTerm])
    assert_type(pipe.screen, Filter | None)
    return pipe


class TwoOutputs(CustomFactor):
    inputs = [EquityPricing.close]
    window_length = 5
    outputs = ["low", "high"]


low, high = TwoOutputs()
assert_type(low, RecarrayField)
