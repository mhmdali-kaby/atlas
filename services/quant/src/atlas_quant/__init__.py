"""atlas_quant: deterministic market statistics over pandas frames (decision #245, brief §7.3).

Pure functions, no I/O except :func:`atlas_quant.data.load_candles`. Statistics are float64: this is
analysis, never money or order sizes.
"""

from atlas_quant.analogs import Analog, AnalogsResult, Distribution, find_analogs
from atlas_quant.data import Candles, DataQuality, GapRange, load_candles
from atlas_quant.leadlag import LagCorrelation, LeadLagResult, cross_asset_lead_lag
from atlas_quant.seasonality import SeasonalityResult, SeasonBucket, seasonality
from atlas_quant.stats import (
    DrawdownSummary,
    HourBucket,
    ReturnsSummary,
    StatsResult,
    WeekdayBucket,
    compute_stats,
)

__all__ = [
    "Analog",
    "AnalogsResult",
    "Candles",
    "DataQuality",
    "Distribution",
    "DrawdownSummary",
    "GapRange",
    "HourBucket",
    "LagCorrelation",
    "LeadLagResult",
    "ReturnsSummary",
    "SeasonBucket",
    "SeasonalityResult",
    "StatsResult",
    "WeekdayBucket",
    "compute_stats",
    "cross_asset_lead_lag",
    "find_analogs",
    "load_candles",
    "seasonality",
]
