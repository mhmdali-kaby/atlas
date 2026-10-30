"""The Derivatives & Sentiment Analyst's hooks (brief §7.5, ATL-136). Disabled by default
(agents.roster): the owner turns it on in Settings → Agents. Config: agents.derivatives_sentiment.

* Market Scout's ``watchlist`` card: one run per pick, best rank first (at most
  ``watchlist_max_runs``).
* @Derivatives: one run per instrument named in the message (at most ``mention_max_instruments``),
  each card replying to the mention.
* Funding settlement: ``funding_delay_minutes`` after each of ``funding_settlement_hours_utc``
  (00:05, 08:05, 16:05 UTC), one run per watchlist and held instrument. Checked every
  ``schedule_check_seconds``; a check more than ``funding_grace_minutes`` after the slot skips it.
* Extreme readings (code trigger): every ``extreme_check_seconds`` the features of every watchlist
  and held instrument are re-read; |OI change| over ``extreme_oi_window_hours`` above
  ``extreme_oi_change_pct`` or |funding z| above ``extreme_funding_z`` starts a run.

Every trigger runs at most once per instrument per ``debounce_minutes`` (per trigger, in this
process's memory). An owner's @mention is never debounced: the owner asked. Runs happen in the
background, one after another (the agent's lock); :func:`settled` waits for them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from atlas_shared.db import Watchlist, WatchlistItem, owner_day
from atlas_shared.events import EventEnvelope
from sqlalchemy import select

from atlas_agents import events
from atlas_agents import technical_analyst as ta_agent
from atlas_agents.agents.technical_analyst import held_instruments, watchlist_picks
from atlas_agents.derivatives import report
from atlas_agents.derivatives.features import DerivativesFeatures
from atlas_agents.historian import message_payload
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult, Trigger
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError

log = logging.getLogger(__name__)

SLUG = report.SLUG
WATCHLIST_EVENT = "warroom.message.watchlist"
FUNDING_JOB = "derivatives_funding"
EXTREME_JOB = "derivatives_extremes"
WARROOM_STREAM = "atlas.warroom"

_tasks: set[asyncio.Task[Any]] = set()
_last_run: dict[tuple[str, str, str], datetime] = {}
"""(env, trigger name, inst_id) -> when that trigger last started a run for the instrument."""
_funding_done: dict[str, datetime] = {}
"""env -> the last funding slot handled."""


def _cfg() -> Mapping[str, Any]:
    return report.config()


def reset() -> None:
    """Forget every debounce and handled funding slot (tests)."""
    _last_run.clear()
    _funding_done.clear()


def _background(work: Coroutine[Any, Any, Any], what: str) -> None:
    async def go() -> None:
        try:
            await work
        except Exception:
            log.exception("derivatives_sentiment: %s failed", what)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settled() -> None:
    """Wait for every background Derivatives & Sentiment run to finish."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def debounce(env: str, trigger: str, inst_ids: list[str], now: datetime) -> list[str]:
    """The instIds ``trigger`` may run now (not run in the last ``debounce_minutes``); marks them."""
    window = timedelta(minutes=int(_cfg()["debounce_minutes"]))
    allowed: list[str] = []
    for inst_id in inst_ids:
        key = (env, trigger, inst_id)
        last = _last_run.get(key)
        if last is not None and now - last < window:
            log.info("derivatives_sentiment: %s for %s debounced (last %s)", trigger, inst_id, last)
            continue
        _last_run[key] = now
        allowed.append(inst_id)
    return allowed


async def _sequence(host: AgentHost, inst_ids: list[str], **kwargs: Any) -> list[RunResult]:
    results: list[RunResult] = []
    for inst_id in inst_ids:
        result = await report.analyse(host, inst_id, **kwargs)
        if result is not None:
            results.append(result)
    return results


def _start(
    host: AgentHost,
    name: str,
    inst_ids: list[str],
    *,
    trigger: Trigger,
    correlation_id: str | None,
    why: str,
    **kwargs: Any,
) -> list[str]:
    ctx = host.tool_context(SLUG)
    allowed = debounce(host.env, name, inst_ids, ctx.now())
    if allowed:
        _background(
            _sequence(host, allowed, trigger=trigger, correlation_id=correlation_id, why=why, **kwargs),
            f"{name} {correlation_id}",
        )
    return allowed


# -- watched instruments -----------------------------------------------------------------------------


def watchlist_instruments(ctx: ToolContext) -> list[str]:
    """Today's (Asia/Baghdad) watchlist, best rank first, owner-removed items left out. Synchronous."""
    with ctx.session_factory() as session:
        wl_id = session.scalar(
            select(Watchlist.id)
            .where(
                Watchlist.env == ctx.env,
                Watchlist.date == owner_day(ctx.now()),
                ctx.bound(Watchlist.created_at),
            )
            .order_by(Watchlist.version.desc())
            .limit(1)
        )
        if wl_id is None:
            return []
        rows = session.scalars(
            select(WatchlistItem.inst_id)
            .where(WatchlistItem.watchlist_id == wl_id, WatchlistItem.removed_by_owner_at.is_(None))
            .order_by(WatchlistItem.rank)
        ).all()
    return list(dict.fromkeys(rows))


def watched_instruments(ctx: ToolContext) -> list[str]:
    """Watchlist instruments (by rank), then held instruments (sorted), each once. Synchronous."""
    listed = watchlist_instruments(ctx)
    held = sorted(held_instruments(ctx) - set(listed))
    return [*listed, *held]


# -- triggers ----------------------------------------------------------------------------------------


@HOOKS.event(SLUG, WATCHLIST_EVENT)
async def on_watchlist(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    inst_ids = watchlist_picks(envelope.payload, limit=int(_cfg()["watchlist_max_runs"]))
    if not inst_ids:
        return None
    card = envelope.payload.get("card")
    watchlist_id = card.get("watchlist_id") if isinstance(card, Mapping) else None
    _start(
        host,
        "watchlist",
        inst_ids,
        trigger="alert",
        correlation_id=envelope.correlation_id or f"derivatives:watchlist:{watchlist_id or envelope.id}",
        why=f"Market Scout published today's watchlist ({', '.join(inst_ids)}).",
    )
    return None


@HOOKS.mention(SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult | None:
    ctx = host.tool_context(SLUG)
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, ctx, request)
    inst_ids = ta_agent.mentioned_instruments(
        request.content_md, limit=int(_cfg()["mention_max_instruments"])
    )
    if not inst_ids:
        if not host.replaying:
            _ask_for_instrument(host, request, reply_to, thread_id)
        return None
    who = "the owner" if request.author_type == "human" else request.author
    why = f"War Room message from {who}: > {request.content_md.strip()}"
    kwargs: dict[str, Any] = {
        "trigger": "mention",
        "correlation_id": request.correlation_id,
        "why": why,
        "reply_to": reply_to,
        "thread_id": thread_id,
    }
    if request.author_type == "human":  # the owner asked: never debounced
        _background(_sequence(host, inst_ids, **kwargs), f"mention {request.event_id}")
    else:
        _start(host, "mention", inst_ids, **kwargs)
    return None


def _ask_for_instrument(
    host: AgentHost, request: MentionRequest, reply_to: str | None, thread_id: str | None
) -> None:
    overrides: dict[str, Any] = {
        "kind": "text",
        "content_md": "Name the instrument to read, for example `@Derivatives BTC-USDT-SWAP`.",
        "reply_to": reply_to,
        "mentions": [],
    }
    if thread_id is not None:
        overrides["thread_id"] = thread_id
    else:
        overrides["thread"] = {"kind": "main", "key": "main"}
    payload = message_payload(SLUG, overrides, "")
    payload["run_id"] = None
    host.publish(
        WARROOM_STREAM,
        events.envelope(
            type_="warroom.message",
            env=host.env,
            agent=SLUG,
            payload=payload,
            correlation_id=request.correlation_id,
        ),
    )


def funding_slot(now: datetime, cfg: Mapping[str, Any] | None = None) -> datetime | None:
    """The settlement + delay slot due at ``now`` (the latest one not after it, if no more than
    ``funding_grace_minutes`` ago), else None."""
    c = cfg or _cfg()
    at = now.astimezone(UTC)
    delay = timedelta(minutes=int(c["funding_delay_minutes"]))
    grace = timedelta(minutes=int(c["funding_grace_minutes"]))
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = [
        day + timedelta(hours=int(hour)) + delay
        for day in (midnight - timedelta(days=1), midnight)
        for hour in c["funding_settlement_hours_utc"]
    ]
    due = [slot for slot in slots if slot <= at]
    if not due:
        return None
    latest = max(due)
    return latest if at - latest <= grace else None


@HOOKS.schedule(SLUG, FUNDING_JOB, every_seconds=int(report.config()["schedule_check_seconds"]))
async def on_funding_settlement(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    slot = funding_slot(fire.at)
    if slot is None or _funding_done.get(host.env) == slot:
        return None
    _funding_done[host.env] = slot
    inst_ids = await asyncio.to_thread(watched_instruments, host.tool_context(SLUG))
    if inst_ids:
        stamp = slot.strftime("%H:%M")
        _start(
            host,
            "funding",
            inst_ids,
            trigger="schedule",
            correlation_id=f"derivatives:funding:{slot.strftime('%Y-%m-%dT%H:%MZ')}",
            why=f"Funding settled {int(_cfg()['funding_delay_minutes'])} minutes before {stamp} UTC: "
            "re-read positioning for this watchlist or held instrument.",
        )
    return None


def extreme_reasons(features: DerivativesFeatures, cfg: Mapping[str, Any] | None = None) -> list[str]:
    """Why ``features`` is an extreme reading (empty when it is not): the OI change over the
    configured window, or a funding z-score beyond the threshold."""
    c = cfg or _cfg()
    reasons: list[str] = []
    oi_limit = Decimal(str(c["extreme_oi_change_pct"]))
    hours = int(c["extreme_oi_window_hours"])
    for window in features.open_interest.windows:
        change = window.oi_change_pct
        if window.hours == hours and change is not None and abs(Decimal(str(change))) > oi_limit:
            reasons.append(f"open interest {change:+.1f}% in {hours}h (threshold {oi_limit}%)")
    z_limit = Decimal(str(c["extreme_funding_z"]))
    for key in c["extreme_funding_z_keys"]:
        z = features.funding.z.get(str(key))
        if z is not None and abs(Decimal(str(z))) > z_limit:
            reasons.append(f"funding z-score ({key}) {z:+.2f} (threshold {z_limit})")
    return reasons


@HOOKS.schedule(SLUG, EXTREME_JOB, every_seconds=int(report.config()["extreme_check_seconds"]))
async def on_extreme_check(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    ctx = host.tool_context(SLUG)
    inst_ids = await asyncio.to_thread(watched_instruments, ctx)
    for inst_id in inst_ids:
        try:
            features = await report.load_features(ctx, inst_id)
        except ToolError as exc:
            log.warning("derivatives_sentiment: extreme check of %s skipped: %s", inst_id, exc)
            continue
        reasons = extreme_reasons(features)
        if not reasons:
            continue
        _start(
            host,
            "extreme",
            [inst_id],
            trigger="alert",
            correlation_id=f"derivatives:extreme:{inst_id}:{fire.at.astimezone(UTC).strftime('%Y-%m-%dT%H:%MZ')}",
            why=f"Extreme positioning reading on {inst_id}: {'; '.join(reasons)}. Say what it means, "
            "and ask Historian for an event study if the follow-through matters.",
        )
    return None


__all__ = [
    "EXTREME_JOB",
    "FUNDING_JOB",
    "WATCHLIST_EVENT",
    "answer_mention",
    "debounce",
    "extreme_reasons",
    "funding_slot",
    "on_extreme_check",
    "on_funding_settlement",
    "on_watchlist",
    "reset",
    "settled",
    "watched_instruments",
    "watchlist_instruments",
]
