"""Events shared by the approval endpoint and the sweeper (ATL-162)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Final

import redis
from atlas_shared import bus
from atlas_shared.db import Proposal
from atlas_shared.events import EventEnvelope

log = logging.getLogger("trading_core.approvals")

PROPOSALS_STREAM: Final = "atlas.proposals"
WARROOM_STREAM: Final = "atlas.warroom"
SOURCE: Final = "service:trading_core.approvals"
WARROOM_SOURCE: Final = "system:orchestrator"
WARROOM_AUTHOR: Final = "orchestrator"


def iso(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat().replace("+00:00", "Z")


def correlation_id(row: Proposal) -> str:
    return f"cycle-{row.cycle_id}" if row.cycle_id is not None else str(row.id)


def proposal_payload(row: Proposal, **extra: Any) -> dict[str, Any]:
    return {
        "proposal_id": str(row.id),
        "version": row.version,
        "cycle_id": str(row.cycle_id) if row.cycle_id is not None else None,
        "inst_id": row.inst_id,
        "side": row.side,
        "status": row.status,
        "status_reason": row.status_reason,
        "approval_expires_at": iso(row.approval_expires_at),
        **extra,
    }


def publish(rds: redis.Redis, stream: str, type_: str, row: Proposal, payload: dict[str, Any], source: str) -> None:
    envelope = EventEnvelope.model_validate(
        {
            "type": type_,
            "env": row.env,
            "source": source,
            "correlation_id": correlation_id(row),
            "payload": payload,
        }
    )
    try:
        bus.publish(stream, envelope, client=rds)
    except redis.RedisError:
        log.warning("event publish failed", extra={"type": type_})


def warroom_line(rds: redis.Redis, row: Proposal, content_md: str, card: dict[str, Any]) -> None:
    """One system line in the Main thread, author orchestrator (the owner's point of contact)."""
    publish(
        rds,
        WARROOM_STREAM,
        "warroom.message",
        row,
        {
            "thread": {"kind": "main", "key": "main"},
            "author_type": "system",
            "author": WARROOM_AUTHOR,
            "kind": "text",
            "content_md": content_md,
            "card": card,
            "reply_to": None,
            "mentions": [],
            "reasoning_md": None,
            "tool_calls": None,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        },
        WARROOM_SOURCE,
    )
