"""The Devil's Advocate's hooks (brief §7.7, ATL-159). Disabled by default (agents.roster): the owner
turns it on in Settings → Agents.

* ``proposal.opened`` on ``atlas.proposals`` (a new proposal or a revision): one review run, the
  card replying to the proposal's War Room card. An event older than
  ``agents.devils_advocate.review_deadline_seconds`` when it is picked up (a restart replaying the
  stream) is skipped and logged.
* The Strategist's reply (``warroom.message.text`` by ``strategist`` whose ``reply_to`` is a round-1
  ``da_review`` message): exactly one re-rate per reviewed proposal version. A reply to a re-rate,
  or a second reply, starts nothing.

Every run is :func:`atlas_agents.devils_advocate.review` or ``rerate``. They run in the background,
one after another (the agent's lock); :func:`settled` waits for them (tests, shutdown).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from datetime import UTC
from typing import Any, Final

from atlas_shared.devils_review import CARD_TYPE
from atlas_shared.events import EventEnvelope

from atlas_agents import devils_advocate as da
from atlas_agents.registry import HOOKS, AgentHost
from atlas_agents.session import RunResult

log = logging.getLogger(__name__)

SLUG: Final = da.SLUG
OPENED_EVENT: Final = "proposal.opened"
REPLY_EVENT: Final = "warroom.message.text"

_tasks: set[asyncio.Task[Any]] = set()
_rerated: set[tuple[str, str]] = set()
"""(env, proposal_id) already re-rated (or being re-rated) by this process."""


def _background(work: Coroutine[Any, Any, Any], what: str) -> None:
    async def go() -> None:
        try:
            await work
        except Exception:
            log.exception("devils_advocate: %s failed", what)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settled() -> None:
    """Wait for every background Devil's Advocate run to finish."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


@HOOKS.event(SLUG, OPENED_EVENT)
async def on_proposal_opened(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    payload = envelope.payload
    proposal_id = payload.get("proposal_id")
    if not isinstance(proposal_id, str):
        return None
    ctx = host.tool_context(SLUG)
    opened_at = envelope.ts if envelope.ts.tzinfo else envelope.ts.replace(tzinfo=UTC)
    age = (ctx.now() - opened_at).total_seconds()
    deadline = int(da.config()["review_deadline_seconds"])
    if age > deadline and not host.replaying:
        log.warning(
            "devils_advocate: proposal.opened %s is %.0f s old (deadline %d s); no review",
            proposal_id,
            age,
            deadline,
        )
        return None
    message_id = payload.get("message_id")
    _background(
        da.review(
            host,
            proposal_id,
            message_id=message_id if isinstance(message_id, str) else None,
            correlation_id=envelope.correlation_id,
            opened_at=opened_at,
        ),
        f"review {proposal_id}",
    )
    return None


@HOOKS.event(SLUG, REPLY_EVENT)
async def on_strategist_reply(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    payload = envelope.payload
    reply_to = payload.get("reply_to")
    if (
        payload.get("author") != da.STRATEGIST
        or payload.get("author_type") != "agent"
        or not isinstance(reply_to, str)
    ):
        return None
    ctx = host.tool_context(SLUG)
    reviewed = await asyncio.to_thread(da.message_row, ctx, reply_to)
    card = reviewed.get("card") if reviewed else None
    if reviewed is None or reviewed.get("author") != SLUG or not isinstance(card, Mapping):
        return None
    if card.get("type") != CARD_TYPE:
        return None
    proposal_id = str(card.get("proposal_id"))
    if card.get("round") != 1:
        log.info("devils_advocate: reply to the re-rate of %s; the debate on it is over", proposal_id)
        return None
    key = (host.env, proposal_id)
    if key in _rerated or await asyncio.to_thread(da.rerate_exists, ctx, proposal_id):
        log.info("devils_advocate: %s was already re-rated once; no further re-rate", proposal_id)
        return None
    _rerated.add(key)
    content = payload.get("content_md")
    _background(
        da.rerate(
            host,
            reviewed,
            content if isinstance(content, str) else "",
            correlation_id=envelope.correlation_id,
        ),
        f"re-rate {proposal_id}",
    )
    return None


__all__ = ["OPENED_EVENT", "REPLY_EVENT", "on_proposal_opened", "on_strategist_reply", "settled"]
