"""The Strategist's code rules on an entry proposal, applied before it is checked and saved (ATL-158).

``warroom.submit_proposal`` calls :func:`apply` for every Strategist entry (submit and revise), so
the rules hold whether the proposal came from the Strategist's run (atlas_agents.strategist.run) or
from the model calling the tool itself:

* ``size_from_guidance`` — size_suggestion_pct_equity is the code's sizing guidance
  (atlas_agents.strategist.sizing), never the model's arithmetic;
* ``leverage_lowest_that_fits`` — leverage_suggestion is the lowest leverage that fits;
* ``probability_clamped`` — probability_estimate stays within +/- max_adjust_points of the
  Historian analog hit rate for the proposal's side;
* ``take_profit_split_default`` / ``time_stop_default`` — the configured defaults fill what the
  model left out.

Every change is one entry in ``meta.corrections`` ({rule, model, code, ...}) on the proposal card.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from atlas_agents.strategist import sizing
from atlas_agents.strategist.inputs import HISTORY, StrategistInputs, gather
from atlas_agents.tools.framework import ToolContext


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def apply_with(
    raw: Mapping[str, Any], inputs: StrategistInputs, cfg: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the corrected proposal dict, card meta) for ``raw`` given ``inputs``. Pure."""
    c = cfg or sizing.config()
    out = dict(raw)
    corrections: list[dict[str, Any]] = []
    side = out.get("side")

    if inputs.sizing is not None:
        code = inputs.sizing.size_pct
        model = _dec(out.get("size_suggestion_pct_equity"))
        if model != code:
            corrections.append({"rule": "size_from_guidance", "model": _s(model), "code": _s(code)})
        out["size_suggestion_pct_equity"] = format(code, "f")

    model_lev = out.get("leverage_suggestion")
    if model_lev != inputs.leverage:
        corrections.append({"rule": "leverage_lowest_that_fits", "model": model_lev, "code": inputs.leverage})
    out["leverage_suggestion"] = inputs.leverage

    tps = out.get("take_profits")
    if isinstance(tps, list) and tps and all(isinstance(t, Mapping) for t in tps):
        if all(t.get("pct") is None for t in tps):
            split = sizing.split_take_profits(len(tps), c)
            out["take_profits"] = [{**t, "pct": format(p, "f")} for t, p in zip(tps, split, strict=True)]
            corrections.append(
                {"rule": "take_profit_split_default", "model": None, "code": [format(p, "f") for p in split]}
            )

    if out.get("time_stop_minutes") is None:
        out["time_stop_minutes"] = inputs.time_stop_minutes
        corrections.append({"rule": "time_stop_default", "model": None, "code": inputs.time_stop_minutes})

    anchor: dict[str, Any] | None = None
    if side in ("buy", "sell"):
        verdict = inputs.item(HISTORY)
        found = sizing.analog_hit_rate(verdict.card if verdict else None, side, c)
        if found is not None:
            hit_rate, horizon = found
            anchor = {
                "hit_rate": _s(hit_rate),
                "horizon": horizon,
                "max_adjust_points": str(c["probability"]["max_adjust_points"]),
            }
            p = _dec(out.get("probability_estimate"))
            if p is not None and 0 <= p <= 1:
                clamped = sizing.clamp_probability(p, hit_rate, c)
                if clamped != p:
                    corrections.append(
                        {
                            "rule": "probability_clamped",
                            "model": _s(p),
                            "code": _s(clamped),
                            "hit_rate": _s(hit_rate),
                            "horizon": horizon,
                        }
                    )
                    out["probability_estimate"] = format(clamped.normalize(), "f")

    meta = {
        "corrections": corrections,
        "sizing": None if inputs.sizing is None else inputs.sizing.to_json(),
        "leverage_suggestion": inputs.leverage,
        "analog_hit_rate": anchor,
        "inputs_missing": inputs.missing,
    }
    return out, meta


def apply(
    session: Session, ctx: ToolContext, raw: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """:func:`apply_with` on freshly gathered inputs; None when ``raw`` names no instrument (the
    schema check then refuses it)."""
    inst_id = raw.get("inst_id")
    if not isinstance(inst_id, str) or not inst_id:
        return None
    return apply_with(raw, gather(session, ctx, inst_id))
