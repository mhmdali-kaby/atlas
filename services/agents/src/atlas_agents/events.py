"""The runtime's events on ``atlas.system`` (brief §3.2 envelope, source ``agent:<slug>``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from atlas_shared import bus
from atlas_shared.events import EventEnvelope

SYSTEM_STREAM: Final = "atlas.system"
AGENT_STATE: Final = "system.agent_state"
AGENT_BUDGET: Final = "system.agent_budget"

Publisher = Callable[[str, EventEnvelope], object]


def bus_publisher(stream: str, envelope: EventEnvelope) -> object:
    """The real publisher: XADD to Redis through atlas_shared.bus."""
    return bus.publish(stream, envelope)


def envelope(
    *, type_: str, env: str, agent: str, payload: dict[str, Any], correlation_id: str | None = None
) -> EventEnvelope:
    return EventEnvelope(
        type=type_,
        env=env,
        source=f"agent:{agent}",
        correlation_id=correlation_id,
        payload=payload,
    )
