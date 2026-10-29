"""atlas_quant: deterministic market statistics over pandas frames (decision #245, brief §7.3).

Pure functions, no I/O except :func:`atlas_quant.data.load_candles`. Statistics are float64: this is
analysis, never money or order sizes.
"""

from atlas_quant.data import Candles, DataQuality, GapRange, load_candles
from atlas_quant.stats import (
    DrawdownSummary,
    HourBucket,
    ReturnsSummary,
    StatsResult,
    WeekdayBucket,
    compute_stats,
)

__all__ = [
    "Candles",
    "DataQuality",
    "DrawdownSummary",
    "GapRange",
    "HourBucket",
    "ReturnsSummary",
    "StatsResult",
    "WeekdayBucket",
    "compute_stats",
    "load_candles",
]
