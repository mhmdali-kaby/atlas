"""The Technical Analyst's hooks (brief §7.4, ATL-135). Disabled by default (agents.roster): the owner
turns it on in Settings → Agents.

* Market Scout's ``watchlist`` card (``warroom.message.watchlist``): one run per pick, best rank
  first, at most ``agents.technical_analyst.watchlist_max_runs``.
* @Technical: one run per instrument named in the message (at most ``mention_max_instruments``),
  each card replying to the mention.
* ``candle.closed`` on ``atlas.market`` for ``position_bar`` (1H) of an instrument with an open
  position: one run re-checking the structure.

Every run is :func:`atlas_agents.technical_analyst.analyse`. They run in the background, one after
another (the agent's lock), so the War Room and market consumers are never held up by a model run;
:func:`settled` waits for them (tests, shutdown).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from typing import Any

from atlas_shared.events import EventEnvelope

from atlas_agents import events
from atlas_agents import technical_analyst as ta_agent
from atlas_agents.historian import message_payload
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost
from atlas_agents.session import RunResult
from atlas_agents.tools import ToolContext
from atlas_agents.tools.account import _positions_at, _sync_instant
from atlas_agents.tools.ta_table import config

log = logging.getLogger(__name__)

SLUG = ta_agent.SLUG
WATCHLIST_EVENT = "warroom.message.watchlist"
CANDLE_EVENT = "candle.closed"
SCOUT = "market_scout"

_tasks: set[asyncio.Task[Any]] = set()


def _background(work: Coroutine[Any, Any, Any], what: str) -> None:
    async def go() -> None:
        try:
            await work
        except Exception:
            log.exception("technical_analyst: %s failed", what)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settled() -> None:
    """Wait for every background Technical Analyst run to finish."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _sequence(host: AgentHost, inst_ids: list[str], **kwargs: Any) -> list[RunResult]:
    results: list[RunResult] = []
    for inst_id in inst_ids:
        result = await ta_agent.analyse(host, inst_id, **kwargs)
        if result is not None:
            results.append(result)
    return results


def watchlist_picks(payload: Mapping[str, Any], limit: int | None = None) -> list[str]:
    """The instIds of a Market Scout watchlist card, best rank first (empty for any other message)."""
    if payload.get("author") != SCOUT or payload.get("author_type") != "agent":
        return []
    card = payload.get("card")
    picks = card.get("picks") if isinstance(card, Mapping) else None
    if not isinstance(picks, list):
        return []
    rows = [p for p in picks if isinstance(p, Mapping) and isinstance(p.get("inst_id"), str)]
    rows.sort(key=lambda p: p["rank"] if isinstance(p.get("rank"), int) else 1_000)
    cap = int(config()["watchlist_max_runs"]) if limit is None else limit
    return list(dict.fromkeys(str(p["inst_id"]) for p in rows))[:cap]


@HOOKS.event(SLUG, WATCHLIST_EVENT)
async def on_watchlist(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    inst_ids = watchlist_picks(envelope.payload)
    if not inst_ids:
        return None
    card = envelope.payload.get("card")
    watchlist_id = card.get("watchlist_id") if isinstance(card, Mapping) else None
    correlation_id = envelope.correlation_id or f"technical:watchlist:{watchlist_id or envelope.id}"
    _background(
        _sequence(
            host,
            inst_ids,
            trigger="alert",
            correlation_id=correlation_id,
            why=f"Market Scout published today's watchlist ({', '.join(inst_ids)}).",
        ),
        f"watchlist {watchlist_id}",
    )
    return None


@HOOKS.mention(SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult | None:
    ctx = host.tool_context(SLUG)
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, ctx, request)
    inst_ids = ta_agent.mentioned_instruments(request.content_md)
    if not inst_ids:
        if not host.replaying:
            overrides: dict[str, Any] = {
                "kind": "text",
                "content_md": "Name the instrument to analyse, for example `@Technical BTC-USDT-SWAP`.",
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
                ta_agent.WARROOM_STREAM,
                events.envelope(
                    type_="warroom.message",
                    env=host.env,
                    agent=SLUG,
                    payload=payload,
                    correlation_id=request.correlation_id,
                ),
            )
        return None
    who = "the owner" if request.author_type == "human" else request.author
    _background(
        _sequence(
            host,
            inst_ids,
            trigger="mention",
            correlation_id=request.correlation_id,
            why=f"War Room message from {who}: > {request.content_md.strip()}",
            reply_to=reply_to,
            thread_id=thread_id,
        ),
        f"mention {request.event_id}",
    )
    return None


def held_instruments(ctx: ToolContext) -> set[str]:
    """instIds with a non-zero position at the last account sync. Synchronous."""
    with ctx.session_factory() as session:
        taken_at = _sync_instant(session, ctx)
        return {p.inst_id for p in _positions_at(session, ctx, taken_at) if p.pos != 0}


@HOOKS.event(SLUG, CANDLE_EVENT)
async def on_candle_closed(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    payload = envelope.payload
    inst_id = payload.get("inst_id")
    if payload.get("bar") != str(config()["position_bar"]) or not isinstance(inst_id, str):
        return None
    held = await asyncio.to_thread(held_instruments, host.tool_context(SLUG))
    if inst_id not in held:
        return None
    _background(
        ta_agent.analyse(
            host,
            inst_id,
            trigger="alert",
            correlation_id=f"technical:candle:{inst_id}:{payload.get('ts')}",
            why=(
                f"A {payload.get('bar')} candle of {inst_id} closed (open {payload.get('ts')}) and Atlas "
                "holds a position in it: say whether the structure that justified the trade is intact, "
                "weakening or broken, and suggest a trailing stop if price has moved +1.5R or more."
            ),
        ),
        f"candle {inst_id} {payload.get('ts')}",
    )
    return None


__all__ = [
    "CANDLE_EVENT",
    "WATCHLIST_EVENT",
    "answer_mention",
    "held_instruments",
    "on_candle_closed",
    "on_watchlist",
    "settled",
    "watchlist_picks",
]
