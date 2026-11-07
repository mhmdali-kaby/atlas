"""The Devil's Advocate (brief §7.7, ATL-159): one ``da_review`` card and one vote per proposal version.

A review is started by ``proposal.opened`` on ``atlas.proposals`` (a new proposal, or a revision:
version 2 with ``parent_id``). :func:`review` then:

1. **Precomputes in code** (:func:`build_inputs`) what the model must not work out by hand: the
   stop-noise ratio (stop distance vs ``noise_mult`` x the median 4H high-low range), the chasing
   distance in ATR(1H) (from the nearest level of the newest TechnicalSetup, and the move before
   entry), the correlation overlap with open positions, and the events inside the trade window.
2. Runs the model once with the proposal card and that table. Its answer ends with one fenced
   ```json DevilsReview card. :class:`ReviewReviewer` validates it: all ten §7.7 items present
   (one retry with the errors; a second invalid card ends the run ``degraded``, which marks the
   agent degraded).
3. Applies the code rules (:func:`enforce_review`): the rating is the §7.7 guide computed from the
   items' severities (any ``loss_gt_1r`` item gives 5); ``fix_applied`` is only possible on a
   re-rate or a revised version; a "take" with a rating above ``take_max_rating`` and no applied
   fix becomes "change" (or "skip" with no fix); a "change" with no fix becomes "skip". Every change
   is recorded in the card's ``corrections``.
4. Casts the (corrected) vote through ``warroom.cast_vote`` and posts the card as a
   ``research_report`` reply under the proposal's War Room card.

:func:`rerate` is the one re-rate (``round`` 2) after the Strategist replies to a review.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from atlas_quant.indicators import wilder_atr_values
from atlas_shared.db import EarningsEvent, EconomicEvent, Instrument, Proposal
from atlas_shared.defaults import DEFAULTS
from atlas_shared.devils_review import CARD_TYPE, CHECKLIST, DevilsReview, Vote, guide_rating
from claude_agent_sdk.types import Message
from pydantic import ValidationError
from sqlalchemy import JSON, String, Uuid, column, or_, select, table
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Session

from atlas_agents import numguard
from atlas_agents import proposals as props
from atlas_agents.historian import split_card, tool_calls
from atlas_agents.mentions import parent_wait_seconds
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult, Trigger
from atlas_agents.tools import ToolContext
from atlas_agents.tools.account import _positions_at, _sync_instant, compute_correlations
from atlas_agents.tools.catalogue import CastVoteInput
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.warroom import MAIN_THREAD, _parent, cast_vote

SLUG: Final = "devils_advocate"
STRATEGIST: Final = "strategist"
KIND: Final = "research_report"
SOURCE: Final = "atlas_agents.devils_advocate"

log = logging.getLogger(__name__)

_POLL_SECONDS: Final = 0.25
_Q: Final = Decimal("0.0001")

_messages = table(
    "warroom_messages",
    column("id", Uuid),
    column("env", String),
    column("event_id", Uuid),
    column("thread_id", Uuid),
    column("author", String),
    column("card", JSONB().with_variant(JSON(), "sqlite")),
    column("content_md", String),
    column("created_at", TIMESTAMP(timezone=True)),
)


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["devils_advocate"]
    return cfg


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


def _s(value: Decimal | float | None) -> str | None:
    """A JSON-safe decimal string at 4 places (None for missing or not finite)."""
    if value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        value = Decimal(repr(value))
    return str(value.quantize(_Q).normalize())


# -- precomputed inputs ------------------------------------------------------------------------------


def entry_mid(row: Proposal) -> Decimal | None:
    """The proposal's entry, else its entry-zone midpoint (None for a market order or a PM action)."""
    if row.entry is not None:
        return Decimal(row.entry)
    zone = row.entry_zone
    if isinstance(zone, list | tuple) and len(zone) == 2:
        low, high = _dec(zone[0]), _dec(zone[1])
        if low is not None and high is not None:
            return (low + high) / 2
    return None


def direction(row: Proposal) -> str | None:
    return {"buy": "long", "sell": "short"}.get(row.side or "")


def _candles(session: Session, ctx: ToolContext, inst_id: str, bar: str, bars: int) -> Any:
    now = ctx.now()
    return load_candles(session, inst_id, bar, now - bar_delta(bar) * bars, now, as_of=now, env=ctx.env).df


def _atr(df: Any, window: int) -> float | None:
    if df is None or len(df) < window:
        return None
    value = float(wilder_atr_values(df, window)[-1])
    return value if math.isfinite(value) and value > 0 else None


def stop_noise(session: Session, ctx: ToolContext, row: Proposal, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Stop distance vs ``noise_mult`` x the median high-low range of the last ``range_lookback_bars``
    ``range_bar`` bars. ratio < 1 means the stop sits inside normal noise (§7.7 item 4)."""
    bar, n = str(cfg["range_bar"]), int(cfg["range_lookback_bars"])
    mult = Decimal(str(cfg["noise_mult"]))
    out: dict[str, Any] = {"bar": bar, "noise_mult": str(mult)}
    entry = entry_mid(row)
    reference = entry
    if entry is None:
        df_last = _candles(session, ctx, row.inst_id, bar, 1)
        reference = _dec(float(df_last["c"].iloc[-1])) if len(df_last) else None
    if row.stop is None or reference is None:
        return {**out, "available": False, "reason": "no_stop_or_entry"}
    df = _candles(session, ctx, row.inst_id, bar, n)
    if len(df) == 0:
        return {**out, "available": False, "reason": "no_candles"}
    ranges = (df["h"] - df["l"]).dropna()
    if len(ranges) == 0:
        return {**out, "available": False, "reason": "no_candles"}
    median = Decimal(repr(float(ranges.median())))
    distance = abs(reference - Decimal(row.stop))
    threshold = mult * median
    ratio = distance / threshold if threshold > 0 else None
    return {
        **out,
        "available": ratio is not None,
        "n_bars": len(ranges),
        "reference_price": _s(reference),
        "stop_distance": _s(distance),
        "median_range": _s(median),
        "threshold": _s(threshold),
        "ratio": _s(ratio),
        "verdict": None if ratio is None else ("inside_noise" if ratio < 1 else "ok"),
        "data_as_of": df.index[-1].isoformat() if len(df) else None,
    }


def _setup_levels(session: Session, ctx: ToolContext, inst_id: str) -> list[Decimal]:
    found = props.latest_card(
        session, ctx, author="technical_analyst", card_type="technical_setup", inst_id=inst_id
    )
    if found is None:
        return []
    levels = found.card.get("levels_used") or []
    prices = [_dec(level.get("price")) for level in levels if isinstance(level, Mapping)]
    return [p for p in prices if p is not None]


def chasing(session: Session, ctx: ToolContext, row: Proposal, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """§7.7 item 5: entry distance from the nearest TechnicalSetup level and the move over the last
    ``chase_move_bars`` bars (in the trade's direction), both in ATR of ``atr_bar``."""
    bar, window = str(cfg["atr_bar"]), int(cfg["atr_window"])
    move_bars = int(cfg["chase_move_bars"])
    entry_limit, move_limit = Decimal(str(cfg["chase_entry_atr"])), Decimal(str(cfg["chase_move_atr"]))
    out: dict[str, Any] = {
        "atr_bar": bar,
        "entry_limit_atr": str(entry_limit),
        "move_limit_atr": str(move_limit),
    }
    df = _candles(session, ctx, row.inst_id, bar, max(int(cfg["atr_lookback_bars"]), move_bars + 1))
    atr_f = _atr(df, window)
    if atr_f is None:
        return {**out, "available": False, "reason": "atr_unavailable"}
    atr = Decimal(repr(atr_f))
    last = Decimal(repr(float(df["c"].iloc[-1])))
    entry = entry_mid(row) or last
    sign = Decimal(-1) if direction(row) == "short" else Decimal(1)
    move: Decimal | None = None
    if len(df) > move_bars:
        before = Decimal(repr(float(df["c"].iloc[-1 - move_bars])))
        move = sign * (last - before) / atr
    levels = _setup_levels(session, ctx, row.inst_id)
    level_dist: Decimal | None = None
    nearest: Decimal | None = None
    if levels:
        nearest = min(levels, key=lambda p: abs(entry - p))
        level_dist = abs(entry - nearest) / atr
    flags = []
    if level_dist is not None and level_dist > entry_limit:
        flags.append("entry_far_from_level")
    if move is not None and move > move_limit:
        flags.append("after_large_move")
    return {
        **out,
        "available": True,
        "atr": _s(atr),
        "last_close": _s(last),
        "entry": _s(entry),
        "entry_vs_last_close_atr": _s(abs(entry - last) / atr),
        "nearest_level": _s(nearest),
        "entry_to_level_atr": _s(level_dist),
        "level_source": "technical_setup.levels_used" if levels else None,
        "move_bars": move_bars,
        "move_before_entry_atr": _s(move),
        "flags": flags,
        "data_as_of": df.index[-1].isoformat(),
    }


def correlation(session: Session, ctx: ToolContext, row: Proposal, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """§7.7 item 6: every open position with its 30-day correlation to the proposal's instrument,
    and the count of same-direction positions in the same asset class."""
    days = int(cfg["correlation_lookback_days"])
    threshold = Decimal(str(cfg["correlation_threshold"]))
    taken_at = _sync_instant(session, ctx)
    held = [p for p in _positions_at(session, ctx, taken_at) if p.pos != 0]
    classes = dict(
        session.execute(
            select(Instrument.inst_id, Instrument.asset_class).where(
                Instrument.env == ctx.env, Instrument.inst_id.in_([row.inst_id, *[p.inst_id for p in held]])
            )
        ).all()
    )
    others = sorted({p.inst_id for p in held if p.inst_id != row.inst_id})
    corr: dict[str, float | None] = {}
    if others:
        result = compute_correlations(ctx, [row.inst_id, *others], days)
        for pair in result.pairs:
            if row.inst_id in (pair.a, pair.b):
                corr[pair.b if pair.a == row.inst_id else pair.a] = pair.corr
    mine = direction(row)
    asset_class = classes.get(row.inst_id)
    positions = []
    for p in held:
        side = "long" if p.pos > 0 else "short"
        if p.pos_side in ("long", "short"):
            side = p.pos_side
        c = corr.get(p.inst_id)
        positions.append(
            {
                "inst_id": p.inst_id,
                "direction": side,
                "asset_class": classes.get(p.inst_id),
                "corr_30d": _s(c),
                "over_threshold": c is not None and Decimal(repr(c)).copy_abs() > threshold,
            }
        )
    return {
        "available": True,
        "lookback_days": days,
        "threshold": str(threshold),
        "asset_class": asset_class,
        "positions": positions,
        "correlated_count": sum(1 for p in positions if p["over_threshold"]),
        "same_class_same_direction": sum(
            1
            for p in positions
            if p["asset_class"] == asset_class and mine is not None and p["direction"] == mine
        ),
        "positions_as_of": taken_at.isoformat() if taken_at else None,
    }


def window_events(
    session: Session, ctx: ToolContext, row: Proposal, cfg: Mapping[str, Any]
) -> dict[str, Any]:
    """§7.7 item 1: economic events (importance >= ``event_min_importance``) and earnings of the
    underlying inside [now, now + the time stop (else ``default_window_minutes``)]."""
    now = ctx.now()
    minutes = row.time_stop_minutes or int(cfg["default_window_minutes"])
    end = now + timedelta(minutes=minutes)
    min_importance = int(cfg["event_min_importance"])
    rows = session.scalars(
        select(EconomicEvent)
        .where(
            EconomicEvent.scheduled_at >= now,
            EconomicEvent.scheduled_at <= end,
            EconomicEvent.importance >= min_importance,
            ctx.bound(EconomicEvent.created_at),
        )
        .order_by(EconomicEvent.scheduled_at)
    ).all()
    economic = [
        {
            "name": e.name,
            "country": e.country,
            "importance": int(e.importance),
            "scheduled_at": e.scheduled_at.astimezone(UTC).isoformat(),
            "minutes_from_now": int((e.scheduled_at - now).total_seconds() // 60),
        }
        for e in rows
    ]
    inst = session.scalars(
        select(Instrument).where(Instrument.env == ctx.env, Instrument.inst_id == row.inst_id)
    ).first()
    earnings: list[dict[str, Any]] = []
    if inst is not None and inst.asset_class in ("stock", "etf") and inst.base_ccy:
        for e in session.scalars(
            select(EarningsEvent).where(
                EarningsEvent.symbol == inst.base_ccy,
                EarningsEvent.date >= now.date(),
                EarningsEvent.date <= end.date(),
                ctx.bound(EarningsEvent.created_at),
            )
        ).all():
            earnings.append({"symbol": e.symbol, "date": e.date.isoformat(), "time": e.time})
    return {
        "available": True,
        "window_start": now.isoformat(),
        "window_end": end.isoformat(),
        "window_minutes": minutes,
        "min_importance": min_importance,
        "economic": economic,
        "earnings": earnings,
    }


def build_inputs(ctx: ToolContext, row: Proposal, defaults: Mapping[str, Any] = DEFAULTS) -> dict[str, Any]:
    """The code-computed table the model reviews with. Synchronous: call it with ``asyncio.to_thread``.
    A part whose data is missing says so (``available: false`` with a reason); one that fails is
    reported as ``error`` rather than failing the review."""
    cfg = config(defaults)
    out: dict[str, Any] = {"as_of": ctx.now().isoformat()}
    parts = (
        ("stop_noise", stop_noise),
        ("chasing", chasing),
        ("correlation", correlation),
        ("events", window_events),
    )
    with ctx.session_factory() as session:
        for name, fn in parts:
            try:
                out[name] = fn(session, ctx, row, cfg)
            except Exception as exc:  # one missing series must not stop the review
                log.warning("devils_advocate: %s for %s failed", name, row.inst_id, exc_info=True)
                out[name] = {"available": False, "reason": "error", "error": f"{type(exc).__name__}: {exc}"}
    return out


# -- the code rules after the model ------------------------------------------------------------------


def enforce_review(
    review: DevilsReview,
    *,
    version: int,
    round_: int,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> tuple[DevilsReview, list[dict[str, Any]]]:
    """The §7.7 rules code enforces after the model. Returns (review, corrections)."""
    cfg = config(defaults)
    corrections: list[dict[str, Any]] = []
    update: dict[str, Any] = {}
    fix = (review.fix or "").strip() or None
    if fix != review.fix:
        update["fix"] = fix
    fix_applied = review.fix_applied
    if fix_applied and round_ == 1 and version == 1:
        # Nothing can have been fixed on a first review of a first version.
        corrections.append({"rule": "fix_applied_impossible", "from": True, "to": False})
        fix_applied = False
        update["fix_applied"] = False
    rating = guide_rating(review.items)
    if rating != review.risk_rating:
        corrections.append(
            {
                "rule": "rating_guide",
                "from": review.risk_rating,
                "to": rating,
                "loss_gt_1r": [i.item for i in review.problems("loss_gt_1r")],
                "serious": [i.item for i in review.problems("serious")],
            }
        )
        update["risk_rating"] = rating
    vote = review.vote
    take_max = int(cfg["take_max_rating"])
    if vote == "take" and rating > take_max and not fix_applied:
        to: Vote = "change" if fix else "skip"
        corrections.append(
            {
                "rule": "take_needs_low_rating",
                "from": "take",
                "to": to,
                "risk_rating": rating,
                "take_max_rating": take_max,
            }
        )
        vote = to
    if vote == "change" and not fix:
        corrections.append({"rule": "change_without_fix", "from": "change", "to": "skip"})
        vote = "skip"
    if vote != review.vote:
        update["vote"] = vote
    return review.model_copy(update=update), corrections


# -- War Room lookups -----------------------------------------------------------------------------


def resolve_message(
    ctx: ToolContext, message_id: str | None, *, wait_seconds: float | None = None
) -> tuple[str | None, str | None]:
    """(row id, thread id) of a War Room message by row or envelope id. The bridge stores a message a
    moment after it is published, so it is retried for up to ``parent_wait_seconds``. (None, None)
    when it never appears. Synchronous."""
    if not message_id:
        return None, None
    wait = 0.0 if ctx.replaying else (parent_wait_seconds() if wait_seconds is None else wait_seconds)
    deadline = time.monotonic() + wait
    while True:
        try:
            return _parent(ctx, message_id)
        except ToolError:
            if time.monotonic() >= deadline:
                return None, None
            time.sleep(_POLL_SECONDS)


def message_row(ctx: ToolContext, message_id: str) -> dict[str, Any] | None:
    """id, thread_id, author, card and content_md of the War Room message with row id ``message_id``."""
    try:
        wanted = uuid.UUID(str(message_id))
    except ValueError:
        return None
    with ctx.session_factory() as session:
        row = session.execute(
            select(
                _messages.c.id,
                _messages.c.thread_id,
                _messages.c.author,
                _messages.c.card,
                _messages.c.content_md,
            )
            .where(_messages.c.env == ctx.env, or_(_messages.c.id == wanted, _messages.c.event_id == wanted))
            .limit(1)
        ).first()
    if row is None:
        return None
    card = row[3] if isinstance(row[3], dict) else None
    return {"id": str(row[0]), "thread_id": str(row[1]), "author": row[2], "card": card, "content_md": row[4]}


def rerate_exists(ctx: ToolContext, proposal_id: str) -> bool:
    """True when a stored round-2 ``da_review`` card exists for ``proposal_id``."""
    with ctx.session_factory() as session:
        found = session.execute(
            select(_messages.c.id)
            .where(
                _messages.c.env == ctx.env,
                _messages.c.author == SLUG,
                _messages.c.card["type"].astext == CARD_TYPE,
                _messages.c.card["proposal_id"].astext == proposal_id,
                _messages.c.card["round"].astext == "2",
            )
            .limit(1)
        ).first()
    return found is not None


def load_row(ctx: ToolContext, proposal_id: str) -> Proposal | None:
    try:
        wanted = uuid.UUID(str(proposal_id))
    except ValueError:
        return None
    with ctx.session_factory() as session:
        row = session.scalars(select(Proposal).where(Proposal.id == wanted, Proposal.env == ctx.env)).first()
        if row is not None:
            session.expunge(row)
        return row


def voting_context(host: AgentHost) -> ToolContext:
    """The Devil's Advocate's ToolContext with the host's publisher, so the code-cast vote's
    ``proposal.vote_cast`` goes where the run's War Room messages go."""
    return dataclasses.replace(host.tool_context(SLUG), publish=host.publish)


# -- the run -----------------------------------------------------------------------------------------

_ITEMS_TEXT: Final = ", ".join(f"{n} {name}" for n, name in enumerate(CHECKLIST, start=1))

_CARD_FIELDS: Final = (
    '{"type": "da_review", "items": [{"item": 1-10, "name": item name, "status": "ok"|"problem", '
    '"severity": "none" (for ok)|"minor"|"serious"|"loss_gt_1r", "detail": "OK" or the problem '
    f'stated with a number}}] (all 10, one each: {_ITEMS_TEXT}), "strongest_objection": str, '
    '"risk_rating": integer 1-5, "fix": str|null (the concrete fix that would change your view), '
    '"fix_applied": bool (true only when the proposal or the Strategist\'s reply already applies it), '
    '"vote": "take"|"change"|"skip", "confidence": 0-1, "reason": str (one line)}'
)


def _proposal_view(row: Proposal) -> dict[str, Any]:
    return props.proposal_card(row, [], props.default_summary(row))


def _output(data: Mapping[str, Any], as_of: Any, source: str) -> dict[str, Any]:
    return {"as_of": as_of, "source": source, "data": data}


def run_prompt(
    row: Proposal,
    inputs: Mapping[str, Any],
    *,
    deadline_seconds: int,
    previous: Mapping[str, Any] | None = None,
    reply_md: str | None = None,
) -> str:
    head = (
        f"Review {row.inst_id} proposal {row.id} (version {row.version}, by {row.author}) "
        "against the 10-item checklist (brief §7.7)."
        if previous is None
        else (
            f"The Strategist replied to your review of {row.inst_id} proposal {row.id} (version "
            f"{row.version}). Re-rate it once; this is your final word on this version."
        )
    )
    proposal = _output(_proposal_view(row), inputs.get("as_of"), "postgres:proposals")
    parts = [
        head,
        f"Your review must be ready within {deadline_seconds} seconds: use the tables below and call a "
        "tool only when an item cannot be answered without one.",
        f"The proposal:\n```json\n{json.dumps(proposal)}\n```",
        "Atlas computed these checks in code (stop noise, chasing in ATR, correlation with open "
        "positions, events in the trade window); use them, do not recompute them:\n"
        f"```json\n{json.dumps(_output(inputs, inputs.get('as_of'), SOURCE))}\n```",
    ]
    if previous is not None:
        parts.append(f"Your previous review card:\n```json\n{json.dumps(previous)}\n```")
        parts.append(f"The Strategist's reply:\n> {(reply_md or '').strip()}")
    parts.append(
        "Write the strongest objection first, in at most 150 words, then end with exactly one fenced "
        f"```json block holding a DevilsReview card with these keys and no others:\n{_CARD_FIELDS}\n"
        "Rating guide: 5 if any item is loss_gt_1r; 4 if two or more are serious; 3 if one is; 2 if "
        "only minor problems; 1 if none. Vote take only when the rating is 2 or less or the fix is "
        "applied; change with the fix; skip otherwise. Do not call warroom tools for this answer: "
        "Atlas checks the card, casts your vote and posts the review."
    )
    return "\n\n".join(parts)


class ReviewReviewer:
    """Validates one DevilsReview card, applies :func:`enforce_review` and the number guard, casts
    the vote and makes the card the run's ``research_report`` reply under the proposal."""

    def __init__(
        self,
        ctx: ToolContext,
        row: Proposal,
        inputs: Mapping[str, Any],
        *,
        round_: int = 1,
        rerate_of: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        opened_at: datetime | None = None,
    ) -> None:
        self.ctx = ctx
        self.row = row
        self.inputs = inputs
        self.round = round_
        self.rerate_of = rerate_of
        self.reply_to = reply_to
        self.thread_id = thread_id
        self.opened_at = opened_at
        self.review: DevilsReview | None = None
        self.vote_error: str | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        review: DevilsReview | None = None
        if card is not None:
            for key in ("meta", "precomputed", "corrections"):  # the runtime's, never the agent's
                card.pop(key, None)
            card["type"] = CARD_TYPE
            card["proposal_id"] = str(self.row.id)
            card["version"] = self.row.version
            card["inst_id"] = self.row.inst_id
            card["round"] = self.round
            card["rerate_of"] = self.rerate_of
            try:
                review = DevilsReview.model_validate(card)
            except ValidationError as exc:
                error = "DevilsReview card failed validation: " + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
                )
        if review is None:
            reason = error or "no DevilsReview card"
            return Review(follow_up=self._follow_up(reason), failure=f"invalid card after retry: {reason}")

        final, corrections = enforce_review(review, version=self.row.version, round_=self.round)
        await self._vote(final)
        outputs: list[Mapping[str, Any]] = [c.output for c in tool_calls(messages) if c.output is not None]
        outputs.append(_output(self.inputs, self.inputs.get("as_of"), SOURCE))
        outputs.append(_output(_proposal_view(self.row), self.inputs.get("as_of"), "postgres:proposals"))
        if not content:
            content = f"**{self.row.inst_id}**: {final.strongest_objection}"
        content = f"{content}\n\n{_verdict_line(final, corrections)}"
        plain = final.model_dump(mode="json", exclude={"meta", "precomputed", "corrections"})
        meta: dict[str, Any] = {**numguard.check(content, plain, outputs).to_meta()}
        if self.opened_at is not None:
            elapsed = (self.ctx.now() - self.opened_at).total_seconds()
            meta["elapsed_seconds"] = round(elapsed, 1)
            meta["late"] = elapsed > int(config()["review_deadline_seconds"])
        if self.vote_error is not None:
            meta["vote_error"] = self.vote_error
        self.review = final.model_copy(
            update={"precomputed": dict(self.inputs), "corrections": tuple(corrections), "meta": meta}
        )
        overrides: dict[str, Any] = {
            "kind": KIND,
            "content_md": content,
            "card": self.review.model_dump(mode="json"),
            "reply_to": self.reply_to,
            "mentions": [],
        }
        if self.thread_id is not None:
            overrides["thread_id"] = self.thread_id
        else:
            overrides["thread"] = dict(MAIN_THREAD)
        return Review(message=overrides)

    async def _vote(self, review: DevilsReview) -> None:
        reason = review.reason or review.strongest_objection
        try:
            await cast_vote(
                self.ctx,
                CastVoteInput(
                    proposal_id=str(self.row.id),
                    vote=review.vote,
                    confidence=review.confidence,
                    reason=f"Risk {review.risk_rating}/5. {reason}"[:4000],
                    fix=review.fix if review.vote == "change" else None,
                ),
            )
        except ToolError as exc:  # e.g. the proposal was revised or closed meanwhile
            self.vote_error = f"{exc.code}: {exc}"
            log.warning("devils_advocate: vote on %s refused: %s", self.row.id, self.vote_error)

    @staticmethod
    def _follow_up(reason: str) -> str:
        return (
            f"Your DevilsReview card was not accepted: {reason}\n\nSend the whole answer again: the "
            "strongest objection, then exactly one fenced ```json block with a corrected DevilsReview "
            f"card answering all 10 items:\n{_CARD_FIELDS}\nThis is your only retry."
        )


def _verdict_line(review: DevilsReview, corrections: Sequence[Mapping[str, Any]]) -> str:
    line = f"**Risk {review.risk_rating}/5 · vote {review.vote}** (confidence {review.confidence:.2f})"
    if review.vote == "change" and review.fix:
        line += f". Fix: {review.fix}"
    rules = [str(c["rule"]) for c in corrections]
    if rules:
        line += f". Atlas corrected: {', '.join(rules)}"
    return line


async def review(
    host: AgentHost,
    proposal_id: str,
    *,
    message_id: str | None,
    correlation_id: str | None,
    opened_at: datetime | None = None,
    trigger: Trigger = "alert",
) -> RunResult | None:
    """Round 1: one review of a new or revised proposal version. None when nothing ran (unknown or
    no longer open proposal)."""

    ctx = voting_context(host)
    row = await asyncio.to_thread(load_row, ctx, proposal_id)
    if row is None or row.status not in list(props.checklist_config()["open_statuses"]):
        log.info("devils_advocate: proposal %s is unknown or not open; no review", proposal_id)
        return None
    reply_to, thread_id = await asyncio.to_thread(resolve_message, ctx, message_id)
    inputs = await asyncio.to_thread(build_inputs, ctx, row)
    deadline = int(config()["review_deadline_seconds"])
    reviewer = ReviewReviewer(ctx, row, inputs, reply_to=reply_to, thread_id=thread_id, opened_at=opened_at)
    return await host.run(
        SLUG,
        run_prompt(row, inputs, deadline_seconds=deadline),
        trigger=trigger,
        task=f"review {row.inst_id} v{row.version}",
        correlation_id=correlation_id,
        reviewer=reviewer,
        max_follow_ups=output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )


async def rerate(
    host: AgentHost,
    reviewed: Mapping[str, Any],
    reply_md: str,
    *,
    correlation_id: str | None,
) -> RunResult | None:
    """Round 2: the one re-rate after the Strategist's reply to the review message ``reviewed`` (its
    stored row: id, thread_id, card). None when nothing ran."""

    ctx = voting_context(host)
    card: Mapping[str, Any] = reviewed["card"]
    row = await asyncio.to_thread(load_row, ctx, str(card.get("proposal_id")))
    if row is None or row.status not in list(props.checklist_config()["open_statuses"]):
        log.info("devils_advocate: proposal %s is unknown or not open; no re-rate", card.get("proposal_id"))
        return None
    inputs = await asyncio.to_thread(build_inputs, ctx, row)
    previous = {k: v for k, v in card.items() if k not in ("precomputed", "meta")}
    reviewer = ReviewReviewer(
        ctx,
        row,
        inputs,
        round_=2,
        rerate_of=str(reviewed["id"]),
        reply_to=str(reviewed["id"]),
        thread_id=str(reviewed["thread_id"]),
    )
    return await host.run(
        SLUG,
        run_prompt(
            row,
            inputs,
            deadline_seconds=int(config()["review_deadline_seconds"]),
            previous=previous,
            reply_md=reply_md,
        ),
        trigger="mention",
        task=f"re-rate {row.inst_id} v{row.version}",
        correlation_id=correlation_id,
        reviewer=reviewer,
        max_follow_ups=output_retries(),
        thread_id=str(reviewed["thread_id"]),
        reply_to=str(reviewed["id"]),
    )


__all__ = [
    "KIND",
    "SLUG",
    "ReviewReviewer",
    "build_inputs",
    "chasing",
    "config",
    "correlation",
    "direction",
    "enforce_review",
    "entry_mid",
    "load_row",
    "message_row",
    "rerate",
    "rerate_exists",
    "resolve_message",
    "review",
    "run_prompt",
    "stop_noise",
    "window_events",
]
