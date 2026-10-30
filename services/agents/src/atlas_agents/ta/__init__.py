"""Technical Analyst precompute (ATL-132, brief §7.4): deterministic indicators per timeframe.

``ta.table(inst_id, frames_by_tf)`` is the entry point; :mod:`atlas_agents.ta.indicators` holds the
per-bar definitions and :mod:`atlas_agents.ta.swings` the ZigZag detector.
"""

from atlas_agents.ta.indicators import compute, indicator_frame, ta_config
from atlas_agents.ta.swings import Swing, zigzag
from atlas_agents.ta.table import table, timeframe_table

__all__ = ["Swing", "compute", "indicator_frame", "ta_config", "table", "timeframe_table", "zigzag"]
