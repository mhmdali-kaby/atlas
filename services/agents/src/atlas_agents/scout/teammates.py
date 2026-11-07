"""Market Scout's requests to News Intelligence and Historian, and the wait for their replies
(ATL-108, brief §7.1 step 3, decision #247 (2)).

Code posts a @News mention (shortlist instruments with news or catalysts) and a @Historian mention
(the top ``historian_top_n``), then polls ``warroom_messages`` for the replies whose ``reply_to`` is
the mention's row, up to ``agents.scout.wait_seconds``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from atlas_shared.events import EventEnvelope
from sqlalchemy import bindparam, text

from atlas_agents.scout.features import FeatureTable
from atlas_agents.scout.prescore import Shortlist
from atlas_agents.scout.publish import publish_config
from atlas_agents.tools import ToolContext
from atlas_agents.tools.warroom import agent_handles, message_envelope


def news_targets(shortlist: Shortlist) -> list[str]:
    """Shortlist instruments with news in the last 24h or their own catalysts in the next 48h."""
    return [
        r.inst_id
        for r in shortlist.rows
        if r.features.news_count_24h > 0
        or r.features.catalysts
        or (r.features.news_max_importance_24h or 0) > 0
    ]


def news_request(shortlist: Shortlist, table: FeatureTable) -> str:
    targets = news_targets(shortlist)
    if targets:
        listed = ", ".join(targets)
        return (
            f"instrument briefs for today's shortlist instruments with news or catalysts: {listed}. "
            "For each: story clusters with CONFIRMED/REPORTED/RUMOUR, upcoming catalysts in 48h, "
            "net sentiment and whether it is priced in."
        )
    top = ", ".join(r.inst_id for r in shortlist.rows[:5])
    return (
        f"no shortlist instrument has news or its own catalyst; please brief the 48h event calendar "
        f"({len(table.shared_catalysts)} shared events) and anything new on {top}."
    )


def historian_request(shortlist: Shortlist) -> str:
    top = ", ".join(r.inst_id for r in shortlist.rows[: int(publish_config()["historian_top_n"])])
    return f"regime and analog check for today's top Market Scout candidates: {top}."


def mention_envelope(
    ctx: ToolContext, target: str, request: str, *, correlation_id: str, depth: int
) -> EventEnvelope:
    handle = agent_handles()[target]
    envelope = message_envelope(
        ctx, kind="text", content_md=f"@{handle} {request}", mentions=[target], mention_depth=depth
    )
    return envelope.model_copy(update={"correlation_id": correlation_id})


_REPLIES_SQL = text(
    "SELECT p.event_id AS mention_id, r.id, r.author, r.created_at "
    "FROM warroom_messages r JOIN warroom_messages p ON r.reply_to = p.id "
    "WHERE r.env = :env AND r.author_type = 'agent' AND p.event_id IN :ids "
    "ORDER BY r.created_at"
).bindparams(bindparam("ids", expanding=True))


@dataclass(frozen=True)
class Reply:
    agent: str
    mention_id: str
    message_id: str
    replied_at: datetime
    kind: str
    content_md: str
    card: Mapping[str, Any] | None


def _find_replies(ctx: ToolContext, asked: Mapping[str, str]) -> dict[str, str]:
    """{agent: reply row id} for the mentions in ``asked`` ({agent: mention envelope id})."""
    by_mention = {mention_id: agent for agent, mention_id in asked.items()}
    found: dict[str, str] = {}
    with ctx.session_factory() as session:
        rows = session.execute(
            _REPLIES_SQL, {"env": ctx.env, "ids": [uuid.UUID(m) for m in by_mention]}
        ).all()
    for mention_id, row_id, author, _created in rows:
        agent = by_mention.get(str(mention_id))
        if agent is not None and author == agent and agent not in found:
            found[agent] = str(row_id)
    return found


def _load_reply(ctx: ToolContext, agent: str, mention_id: str, row_id: str) -> Reply:
    with ctx.session_factory() as session:
        row = session.execute(
            text("SELECT kind, content_md, card, created_at FROM warroom_messages WHERE id = :id"),
            {"id": uuid.UUID(row_id)},
        ).one()
    card = row[2] if isinstance(row[2], dict) else None
    return Reply(agent, mention_id, row_id, row[3], str(row[0]), str(row[1]), card)


def wait_for_replies(
    ctx: ToolContext, asked: Mapping[str, str], *, timeout: float, poll: float
) -> dict[str, Reply]:
    """Poll warroom_messages until every agent in ``asked`` replied or ``timeout`` seconds passed.
    Synchronous: call it with ``asyncio.to_thread``."""
    deadline = time.monotonic() + timeout
    found: dict[str, str] = {}
    while True:
        missing = {a: m for a, m in asked.items() if a not in found}
        if missing:
            found.update(_find_replies(ctx, missing))
        if len(found) == len(asked) or time.monotonic() >= deadline:
            break
        time.sleep(max(0.0, min(poll, deadline - time.monotonic())))
    return {agent: _load_reply(ctx, agent, asked[agent], row_id) for agent, row_id in found.items()}


__all__ = [
    "Reply",
    "historian_request",
    "mention_envelope",
    "news_request",
    "news_targets",
    "wait_for_replies",
]
