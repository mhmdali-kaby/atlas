"""War Room @mentions for any agent (ATL-101, decision #247 (1)).

:func:`mention_from_envelope` turns a ``warroom.message`` on ``atlas.warroom`` that mentions an
agent into a :class:`MentionRequest`, whoever wrote it: the owner, the system or another agent
(``warroom.mention``). The rules are the same for every author; an agent never answers itself.

**Chain depth.** Every message has a mention depth: 0 for a human or system message, and for an
agent's message the depth of the run that wrote it plus one (the ``mention_depth`` field the
``warroom.mention`` tool stamps; an agent message without it counts as 1). A run started by a
mention of depth ``d`` runs at depth ``d``. A mention at depth ``>= agents.mentions.max_depth``
starts no run, so with the default 2 a chain A -> B -> C stops before C.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope

from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.warroom import DEPTH_FIELD, _parent

__all__ = [
    "DEPTH_FIELD",
    "MentionRequest",
    "envelope_depth",
    "max_depth",
    "mention_from_envelope",
    "parent_wait_seconds",
    "resolve_parent",
]


def max_depth(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    """``agents.mentions.max_depth``: a mention at this depth or deeper starts no run."""
    value = defaults["agents"]["mentions"]["max_depth"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("agents.mentions.max_depth must be a positive integer")
    return value


def envelope_depth(envelope: EventEnvelope) -> int:
    """The mention depth of a ``warroom.message``: 0 unless an agent wrote it."""
    payload = envelope.payload
    if payload.get("author_type") != "agent" and not envelope.source.startswith("agent:"):
        return 0
    depth = payload.get(DEPTH_FIELD)
    if isinstance(depth, int) and not isinstance(depth, bool) and depth >= 1:
        return depth
    return 1


@dataclass(frozen=True)
class MentionRequest:
    """A War Room message that mentions an agent. ``row_id``/``thread_id`` come from the payload
    (owner messages carry them) or are resolved from Postgres by the envelope id. ``depth`` is the
    message's mention depth (see the module docstring)."""

    event_id: str
    env: str
    author: str
    author_type: str
    content_md: str
    correlation_id: str | None
    ts: datetime
    row_id: str | None = None
    thread_id: str | None = None
    depth: int = 0


def mention_from_envelope(envelope: EventEnvelope, agent: str, env: str) -> MentionRequest | None:
    """The request for ``agent`` in ``envelope``, or None when it is not a message for it in
    ``env`` (other type, other env, not mentioned, or written by ``agent`` itself)."""
    if envelope.type != "warroom.message" or envelope.env != env:
        return None
    payload = envelope.payload
    mentions = payload.get("mentions") or []
    if not isinstance(mentions, list) or agent not in mentions:
        return None
    if envelope.source == f"agent:{agent}" or payload.get("author") == agent:
        return None
    content = payload.get("content_md")
    if not isinstance(content, str) or not content.strip():
        return None
    row_id = payload.get("message_id")
    thread_id = payload.get("thread_id")
    return MentionRequest(
        event_id=str(envelope.id),
        env=envelope.env,
        author=str(payload.get("author") or "unknown"),
        author_type=str(payload.get("author_type") or "unknown"),
        content_md=content,
        correlation_id=envelope.correlation_id,
        ts=envelope.ts,
        row_id=str(row_id) if row_id else None,
        thread_id=str(thread_id) if thread_id else None,
        depth=envelope_depth(envelope),
    )


def parent_wait_seconds(defaults: Mapping[str, Any] = DEFAULTS) -> float:
    """``agents.mentions.parent_wait_seconds``: how long to wait for the bridge to store a mention."""
    return float(defaults["agents"]["mentions"]["parent_wait_seconds"])


_PARENT_POLL_SECONDS = 0.25


def resolve_parent(
    ctx: ToolContext, request: MentionRequest, *, wait_seconds: float | None = None
) -> tuple[str | None, str | None]:
    """(row id, thread id) of the mention. An agent's mention carries only its envelope id, so it is
    looked up in warroom_messages. The supervisor and atlas:bridge read the same stream, so the row
    may not be stored yet: an agent's mention is retried for up to ``parent_wait_seconds`` (ATL-108:
    Market Scout matches replies by reply_to). When it is still not stored the reply goes to Main.
    Synchronous: call it with ``asyncio.to_thread``."""
    if request.row_id and request.thread_id:
        return request.row_id, request.thread_id
    wait = parent_wait_seconds() if wait_seconds is None else wait_seconds
    if request.author_type != "agent" or ctx.replaying:
        wait = 0.0
    deadline = time.monotonic() + wait
    while True:
        try:
            return _parent(ctx, request.row_id or request.event_id)
        except ToolError:
            if time.monotonic() >= deadline:
                return None, None
            time.sleep(_PARENT_POLL_SECONDS)
