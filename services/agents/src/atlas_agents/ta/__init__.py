"""Technical Analyst precompute (brief §7.4): deterministic indicator, level and pattern tables.

``ta.table(inst_id, frames_by_tf)`` is the entry point. :mod:`atlas_agents.ta.indicators` holds the
per-bar indicator definitions and :mod:`atlas_agents.ta.swings` the ZigZag detector (ATL-132), while
support/resistance zones, prior day/week levels, VWAPs, the volume profile, pattern flags and the
order-book walk live alongside them (ATL-133).
"""

from atlas_agents.ta.indicators import compute, indicator_frame, ta_config
from atlas_agents.ta.swings import Swing, zigzag
from atlas_agents.ta.table import table, timeframe_table

__all__ = ["Swing", "compute", "indicator_frame", "ta_config", "table", "timeframe_table", "zigzag"]
