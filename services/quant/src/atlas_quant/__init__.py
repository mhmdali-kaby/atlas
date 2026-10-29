"""atlas_quant: deterministic market statistics over pandas frames (decision #245, brief §7.3).

Pure functions, no I/O except :func:`atlas_quant.data.load_candles`. Statistics are float64: this is
analysis, never money or order sizes.
"""

from atlas_quant.backtest import (
    BacktestResult,
    Condition,
    Costs,
    Fold,
    Indicator,
    InstrumentBacktest,
    Metrics,
    RuleSpec,
    backtest_rule,
)
from atlas_quant.data import Candles, DataQuality, GapRange, load_candles
from atlas_quant.event_study import AtrMove, EventStudyResult, OffsetStat, event_study
from atlas_quant.stats import (
    DrawdownSummary,
    HourBucket,
    ReturnsSummary,
    StatsResult,
    WeekdayBucket,
    compute_stats,
)

__all__ = [
    "AtrMove",
    "BacktestResult",
    "Candles",
    "Condition",
    "Costs",
    "DataQuality",
    "DrawdownSummary",
    "EventStudyResult",
    "Fold",
    "GapRange",
    "HourBucket",
    "Indicator",
    "InstrumentBacktest",
    "Metrics",
    "OffsetStat",
    "ReturnsSummary",
    "RuleSpec",
    "StatsResult",
    "WeekdayBucket",
    "backtest_rule",
    "compute_stats",
    "event_study",
    "load_candles",
]
