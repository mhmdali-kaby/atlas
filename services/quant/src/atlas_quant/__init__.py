"""atlas_quant: deterministic market statistics over pandas frames (decision #245, brief §7.3).

Pure functions, no I/O except :func:`atlas_quant.data.load_candles`. Statistics are float64: this is
analysis, never money or order sizes.
"""

from atlas_quant.analogs import Analog, AnalogsResult, Distribution, find_analogs
from atlas_quant.data import Candles, DataQuality, GapRange, load_candles
from atlas_quant.drawdown import (
    CurrentDrawdown,
    DrawdownEpisode,
    DrawdownProfile,
    drawdown_profile,
)
from atlas_quant.leadlag import LagCorrelation, LeadLagResult, cross_asset_lead_lag
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
    "Candles",
    "CurrentDrawdown",
    "DataQuality",
    "Distribution",
    "DrawdownEpisode",
    "DrawdownProfile",
    "DrawdownSummary",
    "GapRange",
    "HourBucket",
    "LagCorrelation",
    "LeadLagResult",
    "RegimeParams",
    "RegimeResult",
    "RegimeState",
    "ReturnsSummary",
    "SeasonBucket",
    "SeasonalityResult",
    "StatsResult",
    "VolForecast",
    "VolParams",
    "WeekdayBucket",
    "compute_stats",
    "cross_asset_lead_lag",
    "detect_regime",
    "drawdown_profile",
    "find_analogs",
    "load_candles",
    "seasonality",
    "volatility_forecast",
]
