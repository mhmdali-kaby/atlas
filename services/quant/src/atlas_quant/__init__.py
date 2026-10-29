"""atlas_quant: deterministic market statistics over pandas frames (decision #245, brief §7.3).

Pure functions, no I/O except :func:`atlas_quant.data.load_candles`. Statistics are float64: this is
analysis, never money or order sizes.
"""

from atlas_quant.data import Candles, DataQuality, GapRange, load_candles
from atlas_quant.drawdown import (
    CurrentDrawdown,
    DrawdownEpisode,
    DrawdownProfile,
    drawdown_profile,
)
from atlas_quant.regime import RegimeParams, RegimeResult, RegimeState, detect_regime
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
    "Candles",
    "CurrentDrawdown",
    "DataQuality",
    "DrawdownEpisode",
    "DrawdownProfile",
    "DrawdownSummary",
    "GapRange",
    "HourBucket",
    "RegimeParams",
    "RegimeResult",
    "RegimeState",
    "ReturnsSummary",
    "StatsResult",
    "VolForecast",
    "VolParams",
    "WeekdayBucket",
    "compute_stats",
    "detect_regime",
    "drawdown_profile",
    "load_candles",
    "volatility_forecast",
]
