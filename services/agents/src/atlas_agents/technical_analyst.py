"""The Technical Analyst (brief §7.4, ATL-135): one TechnicalSetup card per instrument, or "No setup".

A run is for one instrument. :func:`analyse` builds the instrument's table in code
(:func:`atlas_agents.tools.ta_table.build_ta_table`, the same table ``okx_market.get_ta_table``
returns) and then:

1. **Stale data refuses.** When the newest closed bar of a timeframe in
   ``agents.technical_analyst.stale_check_bars`` is more than ``stale_bars`` bars late (or there is
   none), no model run starts: a ``system_alert`` "data alert" is posted instead (brief §7.4 failure
   behaviour).
2. Otherwise the model gets the table as a tool result and answers with a short summary ending in
   one fenced ```json TechnicalSetup card. :class:`SetupReviewer` validates it (one retry with the
   errors; a second invalid card ends the run ``degraded``, which marks the agent degraded), then
   :func:`enforce_setup` applies the code rules after the model:

   * every price is rounded to ``tick_sz`` (the stop away from the entry, so it never gets tighter);
   * the stop must sit on the losing side of the entry zone and at least ``min_stop_atr_mult`` x
     ATR(1H) from the entry-zone midpoint; targets must sit on the winning side;
   * R per target and ``expected_r`` are recomputed from the rounded prices (``expected_r`` is the
     close_pct-weighted R when every target has a close_pct, else the final target's R);
   * an ``expected_r`` below the owner's ``min_expected_r`` gives "none";
   * ``est_slippage_bps`` is the book walk at the Risk Engine's max size for the setup's side, and
     above ``max_slippage_bps`` (or when books5 cannot fill that size) a thin-liquidity note is added.

   A rule that removes the setup turns it into setup_type/direction "none" with ``no_setup_reason``
   and keeps the rejected plan in ``meta.corrections``. ``data_as_of`` is always the table's.

The card is posted as a ``research_report`` message (the run's final message).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS
from atlas_shared.technical import SetupTarget, TechnicalSetup
from claude_agent_sdk.types import Message
from pydantic import ValidationError

from atlas_agents import events, numguard
from atlas_agents.historian import message_payload, split_card, tool_calls
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult, Trigger
from atlas_agents.tools import ToolContext
from atlas_agents.tools.account import load_settings
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.ta_table import SOURCE as TABLE_SOURCE
from atlas_agents.tools.ta_table import build_ta_table, config
from atlas_agents.tools.warroom import MAIN_THREAD

SLUG: Final = "technical_analyst"
RESEARCH_REPORT: Final = "research_report"
DATA_ALERT_KIND: Final = "system_alert"
WARROOM_STREAM: Final = "atlas.warroom"
ATR_BAR: Final = "1H"

log = logging.getLogger(__name__)

_INST_ID: Final = re.compile(r"\b[A-Z0-9]{1,20}-[A-Z0-9]{2,10}(?:-(?:SWAP|\d{6}))?\b")


def output_retries(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["output_retries"])


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def default_min_r(defaults: Mapping[str, Any] = DEFAULTS) -> Decimal:
    profiles: Mapping[str, Any] = defaults["risk_profiles"]
    return Decimal(str(profiles[str(profiles["default"])]["min_expected_r"]))


def owner_min_r(ctx: ToolContext) -> Decimal:
    """The owner's ``min_expected_r`` (settings in force at ``ctx.now()``), else the default profile's."""
    try:
        with ctx.session_factory() as session:
            _version, values, _as_of = load_settings(session, ctx)
    except ToolError:
        return default_min_r()
    found = _dec(values.get("min_expected_r"))
    return found if found is not None and found > 0 else default_min_r()


def tick_round(price: Decimal, tick: Decimal | None, rounding: str = ROUND_HALF_UP) -> Decimal:
    """``price`` on the ``tick`` grid (unchanged when the tick is unknown)."""
    if tick is None or tick <= 0:
        return price
    steps = (price / tick).quantize(Decimal(1), rounding=rounding)
    return (steps * tick).quantize(tick)


def mentioned_instruments(text: str, limit: int | None = None) -> list[str]:
    """instIds named in ``text`` (upper-cased, in order, unique), at most ``mention_max_instruments``."""
    cap = int(config()["mention_max_instruments"]) if limit is None else limit
    found = list(dict.fromkeys(m.group(0) for m in _INST_ID.finditer(text.upper())))
    return found[:cap]


# -- the code rules after the model ------------------------------------------------------------------


def _atr(table: Mapping[str, Any]) -> Decimal | None:
    frame = (table.get("timeframes") or {}).get(ATR_BAR) or {}
    return _dec((frame.get("volatility") or {}).get("atr"))


def _slippage(table: Mapping[str, Any], direction: str) -> tuple[Decimal | None, bool | None]:
    """(slippage bps at max size, filled) for the setup's side, from the table's book walk."""
    walks = (table.get("book") or {}).get("slippage")
    if not isinstance(walks, Mapping):
        return None, None
    walk = walks.get("buy" if direction == "long" else "sell")
    if not isinstance(walk, Mapping):
        return None, None
    return _dec(walk.get("slippage_bps")), bool(walk.get("filled"))


def _no_setup(setup: TechnicalSetup, reason: str) -> TechnicalSetup:
    return setup.model_copy(
        update={
            "setup_type": "none",
            "direction": "none",
            "entry_zone": None,
            "stop": None,
            "targets": [],
            "expected_r": None,
            "est_slippage_bps": None,
            "no_setup_reason": reason,
        }
    )


def _plan(setup: TechnicalSetup) -> dict[str, Any]:
    return setup.model_dump(
        mode="json",
        include={"setup_type", "direction", "entry_zone", "stop", "targets", "expected_r", "trigger"},
    )


def enforce_setup(
    setup: TechnicalSetup,
    table: Mapping[str, Any],
    min_r: Decimal,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> tuple[TechnicalSetup, list[dict[str, Any]]]:
    """The §7.4 rules code enforces after the model. Returns (setup, corrections)."""
    cfg = config(defaults)
    corrections: list[dict[str, Any]] = []
    as_of = table.get("data_as_of")
    update: dict[str, Any] = {"inst_id": table.get("inst_id", setup.inst_id), "notes": []}
    if isinstance(as_of, str):
        update["data_as_of"] = datetime.fromisoformat(as_of)
    setup = setup.model_copy(update=update)
    if not setup.is_setup:
        if not setup.no_setup_reason:
            setup = setup.model_copy(update={"no_setup_reason": "No setup."})
        return setup, corrections

    assert setup.entry_zone is not None and setup.stop is not None
    long = setup.direction == "long"
    tick = _dec((table.get("spec") or {}).get("tick_sz"))
    low = tick_round(setup.entry_zone[0], tick)
    high = tick_round(setup.entry_zone[1], tick)
    stop = tick_round(setup.stop, tick, ROUND_FLOOR if long else ROUND_CEILING)
    targets = [t.model_copy(update={"price": tick_round(t.price, tick)}) for t in setup.targets]
    rounded = (low, high, stop, [t.price for t in targets]) != (
        setup.entry_zone[0],
        setup.entry_zone[1],
        setup.stop,
        [t.price for t in setup.targets],
    )
    if rounded:
        corrections.append({"rule": "tick_rounding", "tick_sz": str(tick)})
    original = _plan(setup)

    def reject(rule: str, reason: str, **detail: Any) -> tuple[TechnicalSetup, list[dict[str, Any]]]:
        corrections.append({"rule": rule, "rejected_plan": original, **detail})
        return _no_setup(setup, reason), corrections

    entry = (low + high) / 2
    if (long and stop >= low) or (not long and stop <= high):
        return reject(
            "stop_wrong_side", f"Code rule: the stop {stop} is not beyond the entry zone {low}-{high}."
        )
    distance = abs(entry - stop)
    atr = _atr(table)
    mult = Decimal(str(cfg["min_stop_atr_mult"]))
    if atr is None or atr <= 0:
        return reject(
            "atr_unavailable", "Code rule: ATR(1H) is unavailable, so the stop distance cannot be checked."
        )
    if distance < mult * atr:
        ratio = (distance / atr).quantize(Decimal("0.01"))
        return reject(
            "stop_too_tight",
            f"Code rule: the stop is {ratio} x ATR(1H) from the entry; it must be at least {mult} x ATR(1H).",
            stop_distance_atr=str(ratio),
            min_stop_atr_mult=str(mult),
        )
    wrong = [t.price for t in targets if (t.price <= entry if long else t.price >= entry)]
    if wrong:
        return reject("target_wrong_side", f"Code rule: target {wrong[0]} is not beyond the entry {entry}.")
    targets = [
        t.model_copy(update={"r_multiple": (abs(t.price - entry) / distance).quantize(Decimal("0.01"))})
        for t in targets
    ]
    expected = _expected_r(targets)
    if expected < min_r:
        return reject(
            "below_min_r",
            f"Code rule: expected R {expected} is below the owner's minimum {min_r}.",
            expected_r=str(expected),
            min_r=str(min_r),
        )
    notes: list[str] = []
    slip, filled = _slippage(table, setup.direction)
    limit = Decimal(str(cfg["max_slippage_bps"]))
    if slip is not None and slip > limit:
        notes.append(
            f"Thin liquidity: estimated slippage {slip.quantize(Decimal('0.1'))} bps at the Risk "
            f"Engine's max size is above {limit} bps; the instrument is too thin for full size."
        )
    elif filled is False:
        notes.append("Thin liquidity: the top 5 book levels do not cover the Risk Engine's max size.")
    final = setup.model_copy(
        update={
            "entry_zone": (low, high),
            "stop": stop,
            "targets": targets,
            "expected_r": expected,
            "est_slippage_bps": slip.quantize(Decimal("0.01")) if slip is not None else None,
            "notes": notes,
            "no_setup_reason": None,
        }
    )
    return final, corrections


def _expected_r(targets: Sequence[SetupTarget]) -> Decimal:
    rs = [t.r_multiple or Decimal(0) for t in targets]
    pcts = [t.close_pct for t in targets]
    if all(p is not None for p in pcts):
        total = sum((p for p in pcts if p is not None), Decimal(0))
        if total > 0:
            weighted = sum((r * p for r, p in zip(rs, pcts, strict=True) if p is not None), Decimal(0))
            return (weighted / total).quantize(Decimal("0.01"))
    return rs[-1]


# -- the run ------------------------------------------------------------------------------------------

_CARD_FIELDS: Final = (
    '{"type": "technical_setup", "inst_id": str, "timeframe_bias": {"1D"|"4H"|"1H"|"15m": '
    '"up"|"down"|"range"}, "setup_type": "breakout"|"pullback_to_support"|"breakdown"|'
    '"rally_to_resistance"|"range_fade"|"squeeze_expansion"|"reversal_divergence"|"none", '
    '"direction": "long"|"short"|"none" ("none" exactly when setup_type is "none"), '
    '"entry_zone": [low, high]|null, "trigger": str, "stop": num|null, "targets": [{"price": num, '
    '"r_multiple": num, "reason": "level name", "close_pct": 0-100|null}] (1-3; [] for none), '
    '"expected_r": num|null, "invalidation": str, "quality_score": 0-1, "est_slippage_bps": num|null, '
    '"levels_used": [{"id": "zone id e.g. 1H:z3 or prior_day.high", "price": num|null}], '
    '"no_setup_reason": str|null (for "none": the one condition that would create a setup), '
    '"data_as_of": ISO-8601 with zone}'
)


def _table_output(table: Mapping[str, Any]) -> dict[str, Any]:
    return {"as_of": table.get("data_as_of"), "source": TABLE_SOURCE, "data": table}


def run_prompt(inst_id: str, table: Mapping[str, Any], min_r: Decimal, why: str) -> str:
    return (
        f"{why}\n\nAnalyse {inst_id} (brief §7.4). Atlas computed its indicator and level table "
        "(okx_market.get_ta_table); here it is as a tool result:\n\n"
        f"```json\n{json.dumps(_table_output(table))}\n```\n\n"
        f"The owner's minimum expected R is {min_r}. Write a short summary (at most 120 words) "
        "leading with direction and setup type, then end with exactly one fenced ```json block "
        f"holding a TechnicalSetup card with these keys and no others:\n{_CARD_FIELDS}\n"
        'Every number must come from the table or your tools. If there is no setup, say "No '
        'setup", give setup_type and direction "none" and the one condition that would create '
        "one. Do not call the warroom tools for this answer: Atlas checks the card (tick rounding, "
        "stop distance, minimum R, slippage) and posts it."
    )


class SetupReviewer:
    """Validates one TechnicalSetup card, applies :func:`enforce_setup` and the number guard, and
    makes the card the run's ``research_report`` message."""

    def __init__(
        self,
        inst_id: str,
        table: Mapping[str, Any],
        min_r: Decimal,
        *,
        reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        self.inst_id = inst_id
        self.table = table
        self.min_r = min_r
        self.reply_to = reply_to
        self.thread_id = thread_id
        self.setup: TechnicalSetup | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        setup: TechnicalSetup | None = None
        if card is not None:
            for key in ("meta", "notes"):  # the runtime's, never the agent's
                card.pop(key, None)
            card.setdefault("type", "technical_setup")
            card["inst_id"] = self.inst_id
            card.setdefault("data_as_of", self.table.get("data_as_of"))
            try:
                setup = TechnicalSetup.model_validate(card)
            except ValidationError as exc:
                error = "TechnicalSetup card failed validation: " + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
                )
        if setup is None:
            reason = error or "no TechnicalSetup card"
            return Review(follow_up=self._follow_up(reason), failure=f"invalid card after retry: {reason}")

        final, corrections = enforce_setup(setup, self.table, self.min_r)
        outputs: list[Mapping[str, Any]] = [c.output for c in tool_calls(messages) if c.output is not None]
        outputs.append(_table_output(self.table))
        if not content:
            content = _summary(final)
        elif not final.is_setup and setup.is_setup:
            content = f"{content}\n\n**No setup after Atlas's checks:** {final.no_setup_reason}"
        for note in final.notes:
            content = f"{content}\n\n{note}"
        plain = final.model_dump(mode="json", exclude={"meta"})
        meta = {**numguard.check(content, plain, outputs).to_meta(), "corrections": corrections}
        self.setup = final.model_copy(update={"meta": meta})
        overrides: dict[str, Any] = {
            "kind": RESEARCH_REPORT,
            "content_md": content,
            "card": self.setup.model_dump(mode="json"),
            "reply_to": self.reply_to,
            "mentions": [],
        }
        if self.thread_id is not None:
            overrides["thread_id"] = self.thread_id
        else:
            overrides["thread"] = dict(MAIN_THREAD)
        return Review(message=overrides)

    @staticmethod
    def _follow_up(reason: str) -> str:
        return (
            f"Your TechnicalSetup card was not accepted: {reason}\n\nSend the whole answer again: "
            "the short summary, then exactly one fenced ```json block with a corrected "
            f"TechnicalSetup card:\n{_CARD_FIELDS}\nThis is your only retry."
        )


def _summary(setup: TechnicalSetup) -> str:
    if not setup.is_setup:
        return f"**{setup.inst_id}: no setup.** {setup.no_setup_reason or ''}".strip()
    kind = setup.setup_type.replace("_", " ")
    return f"**{setup.inst_id}: {setup.direction} {kind}**, expected R {setup.expected_r}."


def data_alert(inst_id: str, table: Mapping[str, Any], why: str) -> dict[str, Any]:
    """The ``system_alert`` message for a refused run: which timeframes are how many bars late."""
    stale: Mapping[str, Any] = table.get("stale") or {}
    limit = table.get("stale_limit_bars", config()["stale_bars"])
    parts = [
        f"{bar}: newest closed bar {late} bars late" if late is not None else f"{bar}: no closed bars"
        for bar, late in stale.items()
    ]
    content = (
        f"**Data alert: no technical analysis of {inst_id}.** Candles are stale "
        f"({'; '.join(parts)}; limit {limit} bars). {why}"
    )
    return {
        "kind": DATA_ALERT_KIND,
        "thread": dict(MAIN_THREAD),
        "content_md": content,
        "card": {
            "type": "data_alert",
            "inst_id": inst_id,
            "stale": dict(stale),
            "limit_bars": limit,
            "data_as_of": table.get("data_as_of"),
        },
        "reply_to": None,
        "mentions": [],
    }


async def analyse(
    host: AgentHost,
    inst_id: str,
    *,
    trigger: Trigger,
    correlation_id: str | None,
    why: str,
    reply_to: str | None = None,
    thread_id: str | None = None,
) -> RunResult | None:
    """One instrument: refuse with a data alert on stale candles, else one run whose final message
    is the checked TechnicalSetup card. None when nothing ran (unknown instrument, stale data)."""
    ctx = host.tool_context(SLUG)
    try:
        table, _as_of = await build_ta_table(ctx, inst_id)
    except ToolError as exc:
        _log_skip(inst_id, f"{exc.code}: {exc}")
        return None
    if table.get("stale"):
        if not host.replaying:
            payload = message_payload(SLUG, data_alert(inst_id, table, why), "")
            payload["run_id"] = None  # no run: the refusal is code's, not the model's
            host.publish(
                WARROOM_STREAM,
                events.envelope(
                    type_="warroom.message",
                    env=host.env,
                    agent=SLUG,
                    payload=payload,
                    correlation_id=correlation_id,
                ),
            )
        return None
    min_r = await asyncio.to_thread(owner_min_r, ctx)
    reviewer = SetupReviewer(inst_id, table, min_r, reply_to=reply_to, thread_id=thread_id)
    return await host.run(
        SLUG,
        run_prompt(inst_id, table, min_r, why),
        trigger=trigger,
        task=f"setup {inst_id}",
        correlation_id=correlation_id,
        reviewer=reviewer,
        max_follow_ups=output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )


def _log_skip(inst_id: str, why: str) -> None:
    log.warning("technical_analyst: %s skipped: %s", inst_id, why)


__all__ = [
    "DATA_ALERT_KIND",
    "RESEARCH_REPORT",
    "SLUG",
    "SetupReviewer",
    "analyse",
    "data_alert",
    "enforce_setup",
    "mentioned_instruments",
    "owner_min_r",
    "run_prompt",
    "tick_round",
]
