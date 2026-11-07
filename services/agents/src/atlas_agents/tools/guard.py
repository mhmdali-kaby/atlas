"""Quoting stored external text and raising the War Room alert when it reads like instructions
(brief rule 7, ATL-86).

``quote_fields`` passes each text field of one item through :func:`untrusted`. When any field is
flagged, it returns an :class:`InjectionFlag` for the tool result and publishes exactly one
``warroom.message`` of kind ``system_alert`` (author ``system:guard``) naming the source and the
item id. The alert carries no text from the item itself: the owner reads the item in the app, and
nothing from it can reach the War Room renderer through this path.

The envelope id is derived from (env, item id), so the bridge stores one alert per item however
many times agents read it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Final

from atlas_shared.events import EventEnvelope
from pydantic import BaseModel

from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.untrusted import untrusted

log = logging.getLogger(__name__)

WARROOM_STREAM: Final = "atlas.warroom"
GUARD_SOURCE: Final = "system:guard"
ALERT_KIND: Final = "system_alert"
_ALERT_NAMESPACE: Final = uuid.UUID("6f3a1c52-8a4e-4f0e-9d1b-2b7c5e9a8d40")


class InjectionFlag(BaseModel):
    """One item whose text matched an injection pattern. The matched text itself stays inside the
    quoted fields; this only says where it was."""

    item_id: str
    source: str
    fields: list[str]
    matches: int


def alert_envelope(
    ctx: ToolContext, *, tool: str, source: str, item_id: str, fields: list[str]
) -> EventEnvelope:
    text = (
        f"Instruction-like text detected in {source} item {item_id} "
        f"(fields: {', '.join(fields)}), read by {ctx.agent} via {tool}. "
        "It was passed to the agent as quoted untrusted data and must not be followed."
    )
    return EventEnvelope(
        id=uuid.uuid5(_ALERT_NAMESPACE, f"{ctx.env}:{source}:{item_id}"),
        type="warroom.message",
        env=ctx.env,
        source=GUARD_SOURCE,
        correlation_id=ctx.correlation_id,
        payload={
            "thread": {"kind": "main", "key": "main"},
            "author_type": "system",
            "author": GUARD_SOURCE,
            "kind": ALERT_KIND,
            "content_md": text,
            "card": {
                "alert": "untrusted_instructions",
                "source": source,
                "item_id": item_id,
                "fields": fields,
                "agent": ctx.agent,
                "tool": tool,
            },
            "reply_to": None,
            "mentions": [],
            "reasoning_md": None,
            "tool_calls": None,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        },
    )


def quote_fields(
    ctx: ToolContext,
    *,
    tool: str,
    source: str,
    item_id: str,
    fields: Mapping[str, str | None],
) -> tuple[dict[str, str | None], InjectionFlag | None]:
    """Each field quoted with :func:`untrusted` (None stays None), plus the flag for this item if
    any field matched. A flagged item publishes one War Room alert; a publish failure is logged
    and never fails the tool (the flag still reaches the model)."""
    quoted: dict[str, str | None] = {}
    hit_fields: list[str] = []
    matches = 0
    for name, value in fields.items():
        if value is None:
            quoted[name] = None
            continue
        q = untrusted(value, source)
        quoted[name] = q.text
        if q.flagged:
            hit_fields.append(name)
            matches += len(q.flags)
    if not hit_fields:
        return quoted, None
    try:
        ctx.publish(
            WARROOM_STREAM,
            alert_envelope(ctx, tool=tool, source=source, item_id=item_id, fields=hit_fields),
        )
    except Exception:  # the alert is best effort; the model still sees injection_flags
        log.exception("could not publish the untrusted-text alert for %s item %s", source, item_id)
    return quoted, InjectionFlag(item_id=item_id, source=source, fields=hit_fields, matches=matches)
