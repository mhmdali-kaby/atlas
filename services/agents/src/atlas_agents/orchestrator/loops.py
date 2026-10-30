"""Loop stopping (brief §7.9: "if two agents exchange more than 3 messages on the same point,
summarise both positions and call the vote"; ATL-161).

Code watches every agent reply (``reply_to`` set) on ``atlas.warroom``, per thread. When the latest
replies in a thread alternate between the same two agents more than ``loop_max_replies`` times, the
Orchestrator posts one summary (each side's last point) and calls the vote on the proposal the
exchange is about (:func:`atlas_agents.orchestrator.cycle.call_vote`). An owner message in the
thread starts the count again; one exchange is stopped once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import text

from atlas_agents.orchestrator import cycle
from atlas_agents.registry import AgentHost
from atlas_agents.tools import ToolContext
from atlas_agents.tools.warroom import WARROOM_STREAM, agent_handles, message_envelope

log = logging.getLogger(__name__)

SLUG: Final = "orchestrator"
CARD_TYPE: Final = "loop_summary"
_KEEP: Final = 20
_QUOTE_CHARS: Final = 220
_PARENT_HOPS: Final = 6


@dataclass(frozen=True)
class _Reply:
    author: str
    content: str
    proposal_id: str | None
    reply_to: str | None


@dataclass(frozen=True)
class LoopHit:
    env: str
    thread_key: str
    thread_id: str | None
    thread: Mapping[str, str] | None
    agents: tuple[str, str]
    replies: tuple[_Reply, ...]
    correlation_id: str | None


_threads: dict[tuple[str, str], list[_Reply]] = {}
_stopped: set[tuple[str, str, tuple[str, str]]] = set()


def max_replies(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["orchestrator"]["loop_max_replies"])


def _thread_key(payload: Mapping[str, Any]) -> str | None:
    if payload.get("thread_id"):
        return f"id:{payload['thread_id']}"
    thread = payload.get("thread")
    if isinstance(thread, Mapping):
        return "key:" + json.dumps(dict(thread), sort_keys=True)
    return None


def _tail(replies: list[_Reply]) -> list[_Reply]:
    """The latest replies alternating between the same two authors."""
    tail: list[_Reply] = []
    for reply in reversed(replies):
        if tail and reply.author == tail[-1].author:
            break
        if len({r.author for r in tail} | {reply.author}) > 2:
            break
        tail.append(reply)
    return list(reversed(tail))


def observe(envelope: EventEnvelope) -> LoopHit | None:
    """Track one ``warroom.message``; a :class:`LoopHit` when it makes a loop to stop."""
    payload = envelope.payload
    key = _thread_key(payload)
    if envelope.type != "warroom.message" or key is None:
        return None
    slot = (envelope.env, key)
    author_type = payload.get("author_type")
    if author_type == "human":
        _threads.pop(slot, None)
        return None
    author = payload.get("author")
    if author_type != "agent" or not isinstance(author, str) or author == SLUG or not payload.get("reply_to"):
        return None
    card = payload.get("card") if isinstance(payload.get("card"), Mapping) else {}
    pid = card.get("proposal_id") if isinstance(card, Mapping) else None
    replies = _threads.setdefault(slot, [])
    replies.append(
        _Reply(
            author=author,
            content=str(payload.get("content_md") or ""),
            proposal_id=pid if isinstance(pid, str) else None,
            reply_to=str(payload["reply_to"]),
        )
    )
    del replies[:-_KEEP]
    tail = _tail(replies)
    pair = tuple(sorted({r.author for r in tail}))
    if len(pair) != 2 or len(tail) <= max_replies():
        return None
    stop_key = (envelope.env, key, (pair[0], pair[1]))
    if stop_key in _stopped:
        return None
    _stopped.add(stop_key)
    thread = payload.get("thread") if isinstance(payload.get("thread"), Mapping) else None
    return LoopHit(
        env=envelope.env,
        thread_key=key,
        thread_id=str(payload["thread_id"]) if payload.get("thread_id") else None,
        thread=thread,
        agents=(pair[0], pair[1]),
        replies=tuple(tail),
        correlation_id=envelope.correlation_id,
    )


def _proposal_from_rows(ctx: ToolContext, message_ids: list[str]) -> str | None:
    """The proposal id on the first card found walking up the reply chains of ``message_ids``."""
    frontier = [m for m in message_ids if m]
    seen: set[str] = set()
    with ctx.session_factory() as session:
        for _hop in range(_PARENT_HOPS):
            ids = []
            for value in frontier:
                try:
                    ids.append(str(uuid.UUID(value)))
                except ValueError:
                    continue
            ids = [i for i in ids if i not in seen]
            if not ids:
                return None
            seen.update(ids)
            rows = (
                session.execute(
                    text(
                        "SELECT card->>'proposal_id' AS pid, reply_to FROM warroom_messages "
                        "WHERE env = :env AND (id::text = ANY(:ids) OR event_id::text = ANY(:ids))"
                    ),
                    {"env": ctx.env, "ids": ids},
                )
                .mappings()
                .all()
            )
            for row in rows:
                if row["pid"]:
                    return str(row["pid"])
            frontier = [str(r["reply_to"]) for r in rows if r["reply_to"]]
    return None


def _quote(content: str) -> str:
    line = " ".join(content.split())
    return line if len(line) <= _QUOTE_CHARS else line[: _QUOTE_CHARS - 1].rstrip() + "…"


def summary_text(hit: LoopHit, proposal_id: str | None, vote_now: bool) -> str:
    handles = agent_handles()
    names: Mapping[str, Mapping[str, Any]] = DEFAULTS["warroom"]["agents"]
    sides = []
    for agent in hit.agents:
        last = next((r for r in reversed(hit.replies) if r.author == agent), None)
        point = _quote(last.content) if last is not None else "(no point)"
        sides.append(f'{names[agent]["name"]}: "{point}"')
    about = f" on proposal {proposal_id}" if proposal_id else ""
    if proposal_id and vote_now:
        ending = "Calling the vote now."
    elif proposal_id:
        ending = "The vote opens at the next cycle step; no more replies on this point."
    else:
        ending = "No proposal is linked, so no vote; no more replies on this point."
    return (
        f"Loop stopped: @{handles[hit.agents[0]]} and @{handles[hit.agents[1]]} exchanged "
        f"{len(hit.replies)} replies{about}. " + " ".join(sides) + f" {ending}"
    )


async def stop(host: AgentHost, hit: LoopHit) -> str | None:
    """Post the summary, call the vote; returns the proposal id voted on (None without one)."""
    from atlas_agents.orchestrator.owner import record_score

    ctx = host.tool_context(SLUG)
    proposal_id = next((r.proposal_id for r in reversed(hit.replies) if r.proposal_id), None)
    if proposal_id is None:
        refs = [r.reply_to for r in hit.replies if r.reply_to]
        proposal_id = await asyncio.to_thread(_proposal_from_rows, ctx, refs)
    vote_now = cycle.call_vote(hit.env, proposal_id) if proposal_id else False
    envelope = message_envelope(
        ctx,
        kind="text",
        content_md=summary_text(hit, proposal_id, vote_now),
        card={
            "type": CARD_TYPE,
            "proposal_id": proposal_id,
            "agents": list(hit.agents),
            "replies": len(hit.replies),
            "vote_called": bool(proposal_id),
        },
        thread_id=hit.thread_id,
        thread=hit.thread if hit.thread_id is None else None,
    )
    host.publish(WARROOM_STREAM, envelope)
    try:
        await asyncio.to_thread(record_score, ctx, ctx.now(), loop_stopped=True)
    except Exception:
        log.exception("orchestrator: could not count the stopped loop")
    return proposal_id


def reset() -> None:
    """Forget every tracked thread (tests)."""
    _threads.clear()
    _stopped.clear()


__all__ = ["CARD_TYPE", "LoopHit", "max_replies", "observe", "reset", "stop", "summary_text"]
