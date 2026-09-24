"""Types that pipeline users get from Pipeline and Factor, checked by ty.

Like ``api_types.py``, this module isn't run: CI type-checks it, and each
``assert_type`` fails if an annotation changes what a pipeline author sees.
"""

from typing import assert_type

from zipline.assets import Equity
from zipline.pipeline import Classifier, CustomFactor, Factor, Filter, Pipeline
from zipline.pipeline.data import BoundColumn, Column, DataSet, EquityPricing
from zipline.pipeline.factors import Returns, SimpleMovingAverage
from zipline.pipeline.factors.factor import RecarrayField
from zipline.pipeline.factors.statistical import RollingLinearRegression
from zipline.pipeline.term import ComputableTerm
from zipline.utils.numpy_utils import bool_dtype, categorical_dtype, int64_dtype


def make_pipeline(aapl: Equity) -> Pipeline:
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

    # Terms derived from a term keep its kind (Factor, Filter or Classifier).
    assert_type(returns[aapl], Factor)
    assert_type(returns.downsample("month_start"), Factor)
    assert_type(universe.alias("universe"), Filter)
    assert_type(returns.fillna(0.0), Factor)
    assert_type(returns.isnull(), Filter)
    assert_type(universe.if_else(returns, sma), Factor)

    sector = returns.quartiles()
    assert_type(sector.eq(1), Filter)
    assert_type(sector != 2, Filter)
    assert_type(sector.element_of([1, 2]), Filter)
    assert_type(sector.peer_count(), Factor)
    assert_type(sector.fillna(0), Classifier)
    assert_type(sector.startswith("A"), Filter)
    assert_type(sector.relabel(str.upper), Classifier)

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


# A column's dtype decides what kind of term its ``latest`` is.
class Fundamentals(DataSet):
    revenue = Column(float)
    is_profitable = Column(bool_dtype)
    sector_code = Column(int64_dtype, missing_value=-1)
    industry = Column(categorical_dtype)


assert_type(EquityPricing.close, BoundColumn[Factor])
assert_type(EquityPricing.close.latest, Factor)
assert_type(EquityPricing.close.latest > 5, Filter)
assert_type(EquityPricing.close.fx("EUR"), BoundColumn[Factor])
assert_type(Fundamentals.revenue.latest, Factor)
assert_type(Fundamentals.is_profitable.latest, Filter)
assert_type(Fundamentals.sector_code.latest, Classifier)
assert_type(Fundamentals.industry.latest.startswith("Tech"), Filter)
