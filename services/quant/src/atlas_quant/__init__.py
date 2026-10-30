"""atlas_quant: deterministic market statistics over pandas frames (decision #245, brief §7.3).

Pure functions, no I/O except :func:`atlas_quant.data.load_candles`. Statistics are float64: this is
analysis, never money or order sizes.
"""

from atlas_quant.analogs import Analog, AnalogsResult, Distribution, find_analogs
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
from atlas_quant.drawdown import (
    CurrentDrawdown,
    DrawdownEpisode,
    DrawdownProfile,
    drawdown_profile,
)
from atlas_quant.event_study import AtrMove, EventStudyResult, OffsetStat, event_study
from atlas_quant.leadlag import LagCorrelation, LeadLagResult, cross_asset_lead_lag
from atlas_quant.moves import MoveSince, move_since_atr
from atlas_quant.regime import RegimeParams, RegimeResult, RegimeState, detect_regime
from atlas_quant.seasonality import SeasonalityResult, SeasonBucket, seasonality
from atlas_quant.stats import (
    DrawdownSummary,
    HourBucket,
    ReturnsSummary,
    StatsResult,
    WeekdayBucket,
    compute_stats,
)
from atlas_quant.volatility import VolForecast, VolParams, volatility_forecast

__all__ = [
    "Analog",
    "AnalogsResult",
    "AtrMove",
    "BacktestResult",
    "Candles",
    "Condition",
    "Costs",
    "CurrentDrawdown",
    "DataQuality",
    "Distribution",
    "DrawdownEpisode",
    "DrawdownProfile",
    "DrawdownSummary",
    "EventStudyResult",
    "Fold",
    "GapRange",
    "HourBucket",
    "Indicator",
    "InstrumentBacktest",
    "LagCorrelation",
    "LeadLagResult",
    "Metrics",
    "MoveSince",
    "OffsetStat",
    "RegimeParams",
    "RegimeResult",
    "RegimeState",
    "ReturnsSummary",
    "RuleSpec",
    "SeasonBucket",
    "SeasonalityResult",
    "StatsResult",
    "VolForecast",
    "VolParams",
    "WeekdayBucket",
    "backtest_rule",
    "compute_stats",
    "cross_asset_lead_lag",
    "detect_regime",
    "drawdown_profile",
    "event_study",
    "find_analogs",
    "load_candles",
    "move_since_atr",
    "seasonality",
    "volatility_forecast",
]
