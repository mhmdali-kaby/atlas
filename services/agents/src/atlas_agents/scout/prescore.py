"""Market Scout step 3: the pre-score and the top-25 shortlist (brief §7.1, decision #247 (2), ATL-105).

    score = 0.25*momentum_z + 0.20*vol_expansion_z + 0.20*catalyst_score
          + 0.15*liquidity_score + 0.10*derivatives_signal + 0.10*regime_clarity

The weights, the shortlist size (25), the z clip and the correlation threshold live in
``agents.scout.prescore`` in config/defaults.yaml; changing them changes the ranking with no code
change.

Every component is built in two steps. First a **raw value** per instrument from its ATL-104
feature-table row (below). Then the raw values are **z-scored cross-sectionally** over every row in
the feature table that has one (``(raw - mean) / std``, population std, ddof 0), and clipped to
``+/- clip_z``. The component's contribution is ``weight x z``.

Raw values (``f`` is the row's ``InstrumentFeatures``):

* ``momentum_z``: ``|returns_pct.7d| / atr_pct_1d``, the size of the last week's move in daily ATRs,
  direction-free (the Scout looks for a tradeable move either way; bias is the agent's call). Null
  when either feature is null, or ``atr_pct_1d`` is 0.
* ``vol_expansion_z``: ``vol_ratio_24h_30d`` (realised vol of the last 24h over the last 30 days).
  Null when the feature is null.
* ``catalyst_score``: ``news_max_importance_24h`` (0 when ``news_count_24h`` is 0) +
  ``ln(1 + news_count_24h)`` + ``earnings_points`` if the instrument has its own earnings in the
  catalyst window (``f.catalysts``). Shared economic events are the same for every instrument and
  would cancel in the z-score, so they are not counted. Never null: a news count of 0 is a real 0.
* ``liquidity_score``: ``log10(volume_24h_usd)``. Null when the volume is unknown or not positive.
* ``derivatives_signal``: the mean of the parts that exist among ``|funding_z_7d|`` and
  ``|oi_change_24h_pct| / oi_scale_pct``: how stretched positioning is, either way. Null when both
  are null (spot, or no funding/OI history).
* ``regime_clarity``: ``regime_probability`` (detect_regime's confidence in the current label).
  Null when the feature is null.

**Nulls.** A null raw value is unknown, never 0 (the feature table's rule). Its z is null, it
contributes 0 to the score (the cross-sectional mean), and it is listed in the row's
``null_components`` with the reason, taken from the feature row's ``missing`` where it has one.
When fewer than two rows have a raw value, or every raw value is equal, the component carries no
information: z is 0 for every row that has one and the shortlist notes it.

**Ranking.** Rows are ordered by score descending, then inst_id ascending. The first
``shortlist_size`` are the shortlist (``in_top``). Owner-pinned instruments are always included,
marked ``pinned``, even when ranked below the cut; a pinned instrument that is not in the feature
table (filtered out of the universe, e.g. blocklisted) is listed in ``pinned_missing`` instead, so
the blocklist always wins.

**Correlation flag (step 4).** ``flag_correlated_pairs`` marks shortlist pairs whose 30-day
correlation of daily log returns is above ``correlation.threshold`` (0.8; strictly greater, positive
correlation only). ``shortlist_correlations`` computes them from candles with the same code as the
``atlas_data.get_correlations`` tool (``compute_correlations``). The Scout must not pick more than 2
instruments from one flagged group.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal

from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel

from atlas_agents.scout.features import FeatureTable, InstrumentFeatures
from atlas_agents.scout.universe import scout_config
from atlas_agents.tools.account import CorrelationPair, compute_correlations
from atlas_agents.tools.framework import ToolContext

Component = Literal[
    "momentum_z",
    "vol_expansion_z",
    "catalyst_score",
    "liquidity_score",
    "derivatives_signal",
    "regime_clarity",
]
COMPONENTS: Final[tuple[Component, ...]] = (
    "momentum_z",
    "vol_expansion_z",
    "catalyst_score",
    "liquidity_score",
    "derivatives_signal",
    "regime_clarity",
)


def prescore_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = scout_config(defaults)["prescore"]
    return config


def weights_from(config: Mapping[str, Any]) -> dict[Component, float]:
    raw = config["weights"]
    unknown = set(raw) - set(COMPONENTS)
    if unknown:
        raise ValueError(f"unknown pre-score weight(s): {sorted(unknown)}")
    return {c: float(raw.get(c, 0)) for c in COMPONENTS}


# -- models ------------------------------------------------------------------------------------------


class ComponentValue(BaseModel):
    raw: float | None  # the instrument's raw value (definitions in the module docstring)
    z: float | None  # cross-sectional z, clipped; null when raw is null
    weight: float
    contribution: float  # weight x z, 0 when z is null


class ShortlistRow(BaseModel):
    inst_id: str
    rank: int  # 1-based rank in the whole scored universe
    score: float
    in_top: bool  # within the top shortlist_size
    pinned: bool  # owner-pinned (always included)
    components: dict[str, ComponentValue]
    null_components: dict[str, str]  # component -> why it is null (it contributed 0)
    features: InstrumentFeatures


class PinnedMissing(BaseModel):
    inst_id: str
    reason: str


class CorrelatedPair(BaseModel):
    a: str
    b: str
    corr: float
    n: int


class Shortlist(BaseModel):
    env: str
    as_of: datetime  # the feature table's as_of
    feature_table_valid_until: datetime
    weights: dict[str, float]
    shortlist_size: int
    clip_z: float
    universe_size: int  # feature-table rows scored
    rows: list[ShortlistRow]  # top rows by rank, then pinned rows outside the top by rank
    pinned_missing: list[PinnedMissing]
    correlated_pairs: list[CorrelatedPair] | None = None  # None until shortlist_correlations ran
    correlation_threshold: float
    notes: list[str]


# -- raw components ----------------------------------------------------------------------------------


def _missing(f: InstrumentFeatures, *keys: str) -> str:
    for key in keys:
        if key in f.missing:
            return f"{key}: {f.missing[key]}"
    return f"{', '.join(keys)} unknown"


def raw_components(
    f: InstrumentFeatures, config: Mapping[str, Any]
) -> tuple[dict[Component, float | None], dict[Component, str]]:
    """One row's raw component values and, for each null one, why."""
    raw: dict[Component, float | None] = {}
    why: dict[Component, str] = {}

    ret7 = f.returns_pct.get("7d")
    if ret7 is None or f.atr_pct_1d is None:
        raw["momentum_z"] = None
        why["momentum_z"] = _missing(f, "returns_pct.7d", "atr_pct_1d")
    elif f.atr_pct_1d <= 0:
        raw["momentum_z"] = None
        why["momentum_z"] = "atr_pct_1d is 0"
    else:
        raw["momentum_z"] = abs(ret7) / f.atr_pct_1d

    raw["vol_expansion_z"] = f.vol_ratio_24h_30d
    if f.vol_ratio_24h_30d is None:
        why["vol_expansion_z"] = _missing(f, "vol_ratio_24h_30d")

    importance = 0 if f.news_count_24h == 0 else (f.news_max_importance_24h or 0)
    earnings = any(c.kind == "earnings" for c in f.catalysts)
    raw["catalyst_score"] = (
        float(importance)
        + math.log1p(f.news_count_24h)
        + (float(config["earnings_points"]) if earnings else 0.0)
    )

    vol = f.volume_24h_usd
    if vol is None or vol <= 0:
        raw["liquidity_score"] = None
        why["liquidity_score"] = "volume_24h_usd unknown" if vol is None else "volume_24h_usd is 0"
    else:
        raw["liquidity_score"] = math.log10(float(vol))

    parts: list[float] = []
    if f.funding_z_7d is not None:
        parts.append(abs(f.funding_z_7d))
    if f.oi_change_24h_pct is not None:
        parts.append(abs(f.oi_change_24h_pct) / float(config["oi_scale_pct"]))
    if parts:
        raw["derivatives_signal"] = sum(parts) / len(parts)
    else:
        raw["derivatives_signal"] = None
        why["derivatives_signal"] = _missing(f, "funding_z_7d", "oi_change_24h_pct")

    raw["regime_clarity"] = f.regime_probability
    if f.regime_probability is None:
        why["regime_clarity"] = _missing(f, "regime_probability", "regime_label")

    return raw, why


_FLAT_REL_TOL: Final = 1e-9


def zscores(values: Sequence[float | None], clip: float) -> tuple[list[float | None], bool]:
    """Cross-sectional z of the non-null values (population std), clipped to +/- clip. Returns the
    z list (None where the value is None) and whether the component was informative."""
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [None if v is None else 0.0 for v in values], False
    mean = math.fsum(present) / len(present)
    std = math.sqrt(math.fsum((v - mean) ** 2 for v in present) / len(present))
    if std <= _FLAT_REL_TOL * max(1.0, abs(mean)):  # equal values; float noise is not spread
        return [None if v is None else 0.0 for v in values], False
    return [None if v is None else max(-clip, min(clip, (v - mean) / std)) for v in values], True


# -- shortlist ---------------------------------------------------------------------------------------


def build_shortlist(
    table: FeatureTable,
    pinned: Iterable[str] = (),
    defaults: Mapping[str, Any] = DEFAULTS,
) -> Shortlist:
    """Score every feature-table row and keep the top ``shortlist_size`` plus owner pins. Pure."""
    config = prescore_config(defaults)
    weights = weights_from(config)
    size = int(config["shortlist_size"])
    clip = float(config["clip_z"])
    notes: list[str] = []

    rows = table.rows
    raws: list[dict[Component, float | None]] = []
    whys: list[dict[Component, str]] = []
    for f in rows:
        raw, why = raw_components(f, config)
        raws.append(raw)
        whys.append(why)

    zs: dict[Component, list[float | None]] = {}
    for c in COMPONENTS:
        zs[c], informative = zscores([r[c] for r in raws], clip)
        if rows and not informative:
            notes.append(f"{c}: fewer than two distinct values across the universe; z is 0")

    scored: list[tuple[float, str, int]] = []
    for i, f in enumerate(rows):
        score = math.fsum(weights[c] * (zs[c][i] or 0.0) for c in COMPONENTS)
        scored.append((score, f.inst_id, i))
    scored.sort(key=lambda t: (-t[0], t[1]))

    pin_set = list(dict.fromkeys(pinned))
    present = {f.inst_id for f in rows}
    pinned_missing = [
        PinnedMissing(
            inst_id=p,
            reason="not in the feature table (filtered out of the universe or blocklisted)",
        )
        for p in pin_set
        if p not in present
    ]

    top: list[ShortlistRow] = []
    extra: list[ShortlistRow] = []
    for rank, (score, inst_id, i) in enumerate(scored, start=1):
        in_top = rank <= size
        is_pinned = inst_id in pin_set
        if not in_top and not is_pinned:
            continue
        row = ShortlistRow(
            inst_id=inst_id,
            rank=rank,
            score=score,
            in_top=in_top,
            pinned=is_pinned,
            components={
                c: ComponentValue(
                    raw=raws[i][c],
                    z=zs[c][i],
                    weight=weights[c],
                    contribution=weights[c] * (zs[c][i] or 0.0),
                )
                for c in COMPONENTS
            },
            null_components={c: whys[i][c] for c in COMPONENTS if raws[i][c] is None},
            features=rows[i],
        )
        (top if in_top else extra).append(row)

    if not rows:
        notes.append("the feature table has no rows; the shortlist is empty")

    return Shortlist(
        env=table.env,
        as_of=table.as_of,
        feature_table_valid_until=table.valid_until,
        weights=dict(weights),
        shortlist_size=size,
        clip_z=clip,
        universe_size=len(rows),
        rows=top + extra,
        pinned_missing=pinned_missing,
        correlation_threshold=float(config["correlation"]["threshold"]),
        notes=notes,
    )


# -- correlation flag --------------------------------------------------------------------------------


def flag_correlated_pairs(pairs: Iterable[CorrelationPair], threshold: float) -> list[CorrelatedPair]:
    """Pairs whose correlation is strictly above threshold, highest first. Null corr is skipped."""
    flagged = [
        CorrelatedPair(a=p.a, b=p.b, corr=p.corr, n=p.n)
        for p in pairs
        if p.corr is not None and p.corr > threshold
    ]
    flagged.sort(key=lambda p: (-p.corr, p.a, p.b))
    return flagged


def shortlist_correlations(
    ctx: ToolContext, shortlist: Shortlist, defaults: Mapping[str, Any] = DEFAULTS
) -> Shortlist:
    """A copy of the shortlist with ``correlated_pairs`` filled from closed 1D candles (bounded by
    ctx.now()), using the same computation as the get_correlations tool. Synchronous (DB-bound)."""
    config = prescore_config(defaults)["correlation"]
    result = compute_correlations(ctx, [r.inst_id for r in shortlist.rows], int(config["lookback_days"]))
    flagged = flag_correlated_pairs(result.pairs, float(config["threshold"]))
    notes = list(shortlist.notes)
    if result.missing:
        notes.append(f"no 1D candles for correlation: {', '.join(result.missing)}")
    return shortlist.model_copy(update={"correlated_pairs": flagged, "notes": notes})


__all__ = [
    "COMPONENTS",
    "ComponentValue",
    "CorrelatedPair",
    "PinnedMissing",
    "Shortlist",
    "ShortlistRow",
    "build_shortlist",
    "flag_correlated_pairs",
    "prescore_config",
    "raw_components",
    "shortlist_correlations",
    "weights_from",
    "zscores",
]
