"""``okx_market.get_positioning_features``: the Derivatives & Sentiment Analyst's computed features
(brief §7.5, ATL-136) — ATL-134's :func:`atlas_agents.derivatives.features.compute_features` for one
instrument, as of ``ctx.now()``.

Funding (current, annualised, 7d/30d z, sign flips), open interest with the OI-price quadrant for
1h/4h/24h, the long/short account ratio and its 24h change, taker buy/sell ratios (1h, 4h), the
basis (perp mark vs the OKX index, bps) and the ``unreliable`` flags. Every row read is
``ts <= ctx.now()``; in replay mode the live Redis snapshot is not read (it would be look-ahead), so
the basis is null with reason ``stale``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Final

from atlas_shared.db import Instrument
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from atlas_agents.derivatives.features import DerivativesFeatures, compute_features, load_frames
from atlas_agents.tools.catalogue import InstIdInput
from atlas_agents.tools.framework import ToolContext, ToolError, ToolResult, atlas_tool

SOURCE: Final = (
    "postgres:funding_rates,open_interest,long_short_ratio,taker_volume,candles,instruments; "
    "redis:atlas:md (mark price, OKX index)"
)


def _features(ctx: ToolContext, inst_id: str) -> DerivativesFeatures:
    as_of = ctx.now()
    with ctx.session_factory() as session:
        known = session.scalar(
            select(Instrument.inst_id)
            .where(Instrument.env == ctx.env, Instrument.inst_id == inst_id)
            .limit(1)
        )
        if known is None:
            raise ToolError("not_found", f"no instrument {inst_id} in {ctx.env}")
        frames = load_frames(session, ctx.env, inst_id, as_of)
    snapshot = None if ctx.replaying else ctx.market_snapshot(ctx.env, inst_id)
    return compute_features(inst_id, frames, as_of, snapshot=snapshot)


async def build_positioning_features(ctx: ToolContext, inst_id: str) -> tuple[dict[str, Any], datetime]:
    """(the features as JSON-ready data, their as_of). Raises ToolError not_found for an unknown
    instrument."""
    features = await asyncio.to_thread(_features, ctx, inst_id)
    return features.model_dump(mode="json"), features.data_as_of


class PositioningFeaturesOut(BaseModel):
    """:class:`DerivativesFeatures` as JSON (shape in docs/handoff/ATL-134-derivatives-features.md)."""

    model_config = ConfigDict(extra="allow")

    inst_id: str
    ccy: str
    funding: dict[str, Any]
    open_interest: dict[str, Any]
    long_short: dict[str, Any]
    taker: dict[str, Any]
    basis: dict[str, Any]
    unreliable: bool
    unreliable_reasons: dict[str, str]
    data_as_of: str


@atlas_tool("okx_market", "get_positioning_features", InstIdInput, PositioningFeaturesOut)
async def get_positioning_features(ctx: ToolContext, args: InstIdInput) -> ToolResult:
    """Positioning features for inst_id, all computed by code: funding (current, annualised, z-score
    over 7d and 30d, sign flips in the last 3 days), open interest with its 1h/4h/24h change and the
    OI-price quadrant per window (new_longs, short_covering, new_shorts, long_liquidation, flat), the
    long/short account ratio and its 24h change, taker buy/sell volume ratios for 1h and 4h, the
    basis (perp mark vs OKX index, bps; null with a reason when stale or missing) and unreliable
    flags (zero OI or volume). Null values carry a reason under "missing". Use these numbers; do not
    recompute them."""
    plain, as_of = await build_positioning_features(ctx, args.inst_id)
    return ToolResult(data=PositioningFeaturesOut.model_validate(plain), as_of=as_of, source=SOURCE)


__all__ = ["SOURCE", "PositioningFeaturesOut", "build_positioning_features", "get_positioning_features"]
