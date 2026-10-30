"""The Strategist's hooks (brief §7.6, ATL-158). Disabled by default (agents.roster): the owner turns
it on in Settings → Agents.

* The Orchestrator's cycle (ATL-160) calls :func:`run_cycle` with the cycle id: one plan per
  current watchlist instrument (best rank first, at most ``agents.strategist.cycle_max_instruments``),
  each run with correlation id ``cycle-<cycle_id>`` so its proposal carries the cycle_id.
* @Strategist (from the owner or a teammate): one plan per instrument named in the message (at
  most ``mention_max_instruments``), each answer replying to the mention. A Portfolio Manager
  mention is its formalise request; the Strategist can only submit entries (submit_proposal's
  amend/close kinds stay the PM's own, ATL-153), so the run says what to do instead when the PM
  asks for an amend or close.

Every run is :func:`atlas_agents.strategist.run.plan`; mention runs go in the background, one after
another (the agent's lock); :func:`settled` waits for them (tests, shutdown).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from atlas_agents import events
from atlas_agents.historian import message_payload
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost
from atlas_agents.session import RunResult
from atlas_agents.strategist import inputs as inp
from atlas_agents.strategist import run as strategist
from atlas_agents.strategist import sizing
from atlas_agents.technical_analyst import mentioned_instruments
from atlas_agents.tools.warroom import MAIN_THREAD

log = logging.getLogger(__name__)

SLUG = strategist.SLUG
PORTFOLIO_MANAGER = "portfolio_manager"

_tasks: set[asyncio.Task[Any]] = set()


def _background(work: Coroutine[Any, Any, Any], what: str) -> None:
    async def go() -> None:
        try:
            await work
        except Exception:
            log.exception("strategist: %s failed", what)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settled() -> None:
    """Wait for every background Strategist run to finish."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _sequence(host: AgentHost, inst_ids: list[str], **kwargs: Any) -> list[RunResult]:
    results: list[RunResult] = []
    for inst_id in inst_ids:
        result = await strategist.plan(host, inst_id, **kwargs)
        if result is not None:
            results.append(result)
    return results


async def run_cycle(host: AgentHost, cycle_id: str, *, limit: int | None = None) -> list[RunResult]:
    """One plan per watchlist instrument for the Orchestrator's cycle ``cycle_id`` (awaited: the
    cycle's state machine knows when the Strategist is done). A "no trade" decided in code returns
    no RunResult; its message is still posted."""
    ctx = host.tool_context(SLUG)

    def picks() -> list[str]:
        with ctx.session_factory() as session:
            return [i.inst_id for i in inp.watchlist_items(session, ctx)]

    cap = int(sizing.config()["cycle_max_instruments"]) if limit is None else limit
    inst_ids = list(dict.fromkeys(await asyncio.to_thread(picks)))[:cap]
    return await _sequence(
        host,
        inst_ids,
        trigger="schedule",
        correlation_id=strategist.cycle_correlation_id(cycle_id),
        why=f"Analysis cycle {cycle_id}: the research for today's watchlist is in (or timed out).",
    )


def _ask_for_instrument(
    host: AgentHost, request: MentionRequest, reply_to: str | None, thread_id: str | None
) -> None:
    if host.replaying:
        return
    overrides: dict[str, Any] = {
        "kind": "text",
        "content_md": "Name the instrument to plan, for example `@Strategist BTC-USDT-SWAP`.",
        "reply_to": reply_to,
        "mentions": [],
    }
    if thread_id is not None:
        overrides["thread_id"] = thread_id
    else:
        overrides["thread"] = dict(MAIN_THREAD)
    payload = message_payload(SLUG, overrides, "")
    payload["run_id"] = None
    host.publish(
        strategist.WARROOM_STREAM,
        events.envelope(
            type_="warroom.message",
            env=host.env,
            agent=SLUG,
            payload=payload,
            correlation_id=request.correlation_id,
        ),
    )


@HOOKS.mention(SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult | None:
    ctx = host.tool_context(SLUG)
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, ctx, request)
    cap = int(sizing.config()["mention_max_instruments"])
    inst_ids = mentioned_instruments(request.content_md, cap)
    if not inst_ids:
        _ask_for_instrument(host, request, reply_to, thread_id)
        return None
    if request.author_type == "human":
        why = f"The owner asks: > {request.content_md.strip()}\nOwner instructions override teammates."
    elif request.author == PORTFOLIO_MANAGER:
        why = (
            f"Formalise request from the Portfolio Manager: > {request.content_md.strip()}\n"
            "You can submit entry proposals only; an amend_stop, amend_targets, partial_close or close "
            "is the Portfolio Manager's own submit_proposal. If that is what it asks for, decide "
            "no_trade and say so in the reasons."
        )
    else:
        why = f"War Room message from {request.author}: > {request.content_md.strip()}"
    _background(
        _sequence(
            host,
            inst_ids,
            trigger="mention",
            correlation_id=request.correlation_id,
            why=why,
            reply_to=reply_to,
            thread_id=thread_id,
        ),
        f"mention {request.event_id}",
    )
    return None
