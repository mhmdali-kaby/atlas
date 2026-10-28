"""The Redis Streams event envelope every Atlas service publishes (brief §3.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

STREAMS = (
    "atlas.warroom",
    "atlas.market",
    "atlas.account",
    "atlas.proposals",
    "atlas.risk",
    "atlas.orders",
    "atlas.system",
)

Env = Literal["demo", "live"]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    type: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    env: Env
    source: str
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
