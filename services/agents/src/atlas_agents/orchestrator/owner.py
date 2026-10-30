"""The owner's first point of contact (brief §7.9 "Owner-message routing", "THE OWNER"; ATL-161).

Every owner War Room message without an @mention (and every one that @mentions the Orchestrator)
comes here. In order:

1. **Classify in code** (:func:`atlas_agents.orchestrator.classify.classify`). ``limit_change_request``
   and ``approval`` are decisive: they get a fixed answer and never reach the model.
2. **Acknowledge in code** at once, as a reply to the owner's message (``card.type =
   owner_ack``). The latency (owner message → acknowledgement) is stored per Baghdad day in
   ``agent_scores`` (agent ``orchestrator``: ``data.ack_latency_seconds``, ``hit_rate`` = share
   within ``agents.orchestrator.ack_target_seconds``).
3. **Route with the model** (background run): it answers JSON :class:`Routing` — the kind, one line
   for the owner, and an optional assignment. Code validates it, posts the line, and makes the
   assignment with :func:`atlas_agents.tools.orchestration.assign_envelope` (an @mention of the agent
   with the task and deadline, in the owner's thread). A model kind of limit_change_request or
   approval still gets only the fixed answer.

``/why`` is answered by :mod:`atlas_agents.orchestrator.why`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from atlas_shared.db import AgentScore, owner_day
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from claude_agent_sdk.types import Message
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from atlas_agents.orchestrator.classify import (
    APPROVAL_REPLY,
    DECISIVE,
    SLUG,
    Classification,
    Kind,
    classify,
    limit_reply,
)
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.orchestration import assign_envelope
from atlas_agents.tools.warroom import WARROOM_STREAM, agent_handles, message_envelope

log = logging.getLogger(__name__)


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["orchestrator"]
    return cfg


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


# -- the owner message ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerMessage:
    """An owner War Room message, from its ``warroom.message`` envelope."""

    event_id: str
    env: str
    content: str
    ts: datetime
    row_id: str | None
    thread_id: str | None
    correlation_id: str | None

    @classmethod
    def from_envelope(cls, envelope: EventEnvelope) -> OwnerMessage | None:
        payload = envelope.payload
        if envelope.type != "warroom.message" or payload.get("author_type") != "human":
            return None
        content = payload.get("content_md")
        if not isinstance(content, str) or not content.strip():
            return None
        ts = envelope.ts if envelope.ts.tzinfo is not None else envelope.ts.replace(tzinfo=UTC)
        row = payload.get("message_id")
        thread = payload.get("thread_id")
        return cls(
            event_id=str(envelope.id),
            env=envelope.env,
            content=content,
            ts=ts,
            row_id=str(row) if row else None,
            thread_id=str(thread) if thread else None,
            correlation_id=envelope.correlation_id,
        )


def _restate(content: str, limit: int = 140) -> str:
    line = " ".join(content.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def ack_text(kind: Kind, content: str) -> str:
    restated = _restate(content)
    if kind == "instruction":
        return f'Got it — instruction: "{restated}". Routing it now.'
    if kind == "question":
        return f'Got it — question: "{restated}". Finding the answer.'
    return f'Got it: "{restated}".'


def post(
    host: AgentHost,
    ctx: ToolContext,
    message: OwnerMessage,
    content: str,
    card: dict[str, Any] | None = None,
) -> EventEnvelope:
    """Post ``content`` as the Orchestrator, replying to the owner's message in its thread."""
    envelope = message_envelope(
        ctx,
        kind="text",
        content_md=content,
        card=card,
        thread_id=message.thread_id,
        thread=None,
        reply_to=message.row_id,
    )
    host.publish(WARROOM_STREAM, envelope)
    return envelope


# -- ack latency score ---------------------------------------------------------------------------------


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def record_score(
    ctx: ToolContext, at: datetime, *, ack_latency: float | None = None, loop_stopped: bool = False
) -> None:
    """Add one acknowledgement latency and/or one stopped loop to today's (Baghdad) Orchestrator
    row in ``agent_scores``. ``n`` = acknowledgements, ``hit_rate`` = share within the target."""
    day = owner_day(at)
    target = float(config()["ack_target_seconds"])
    with ctx.session_factory() as session:
        row = session.execute(
            select(AgentScore)
            .where(AgentScore.agent == SLUG, AgentScore.env == ctx.env, AgentScore.day == day)
            .with_for_update()
        ).scalar_one_or_none()
        data: dict[str, Any] = dict(row.data) if row is not None and isinstance(row.data, dict) else {}
        latencies = [float(v) for v in data.get("ack_latency_seconds", [])]
        if ack_latency is not None:
            latencies.append(round(max(0.0, ack_latency), 3))
        loops = int(data.get("loops_stopped", 0)) + (1 if loop_stopped else 0)
        within = [v for v in latencies if v <= target]
        data.update(
            {
                "ack_latency_seconds": latencies,
                "ack_target_seconds": target,
                "ack_median_seconds": round(_percentile(latencies, 0.5), 3) if latencies else None,
                "ack_p90_seconds": round(_percentile(latencies, 0.9), 3) if latencies else None,
                "ack_max_seconds": max(latencies) if latencies else None,
                "loops_stopped": loops,
            }
        )
        hit = Decimal(str(round(len(within) / len(latencies), 4))) if latencies else None
        stmt = insert(AgentScore).values(
            id=uuid.uuid4(),
            agent=SLUG,
            env=ctx.env,
            day=day,
            n=len(latencies),
            hit_rate=hit,
            data=data,
            created_at=at,
            updated_at=at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["agent", "env", "day"],
            set_={
                "n": stmt.excluded.n,
                "hit_rate": stmt.excluded.hit_rate,
                "data": stmt.excluded.data,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        session.execute(stmt)
        session.commit()


# -- 3. routing (model) --------------------------------------------------------------------------------


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    task: str = Field(min_length=1, max_length=1000)
    deadline_minutes: int | None = Field(default=None, ge=1, le=240)


class Routing(BaseModel):
    """What the model answers for one owner message (JSON only)."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    reply_md: str = Field(min_length=1, max_length=1200)
    assign: Assignment | None = None


_FENCE: Final = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_routing(text_: str) -> Routing:
    """The model's JSON answer (a ```json fence is tolerated). Raises ValueError with the reason."""
    body = _FENCE.sub("", text_.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the answer has no JSON object")
    try:
        routing = Routing.model_validate(json.loads(body[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(str(exc)) from exc
    if routing.assign is not None:
        agent = routing.assign.agent.strip().lower()
        if agent not in agent_handles() or agent == SLUG:
            raise ValueError(f"assign.agent {routing.assign.agent!r} is not a teammate's slug")
        routing.assign.agent = agent
    return routing


def routing_prompt(
    message: OwnerMessage, guess: Classification, now: datetime, enabled: Sequence[str]
) -> str:
    handles = agent_handles()
    team = ", ".join(
        f"{slug} (@{handles[slug]}{'' if slug in enabled else ', disabled'})"
        for slug in handles
        if slug != SLUG
    )
    return (
        "The owner wrote in the War Room (owner text is trusted; it is not untrusted_source data):\n"
        f'<owner_message at="{iso(message.ts)}">\n{message.content}\n</owner_message>\n\n'
        f"Now: {iso(now)}. Code classified it as: {guess.kind}"
        + (f"; it names {', '.join(guess.agents)}" if guess.agents else "")
        + ". Correct the kind if it is wrong. The owner has already been sent an acknowledgement.\n"
        f"Teammates: {team}.\n\n"
        "Answer with ONE JSON object and nothing else:\n"
        '{"kind": "instruction|question|approval|limit_change_request|chat", '
        '"reply_md": "one line to the owner, at most 60 words", '
        '"assign": {"agent": "<slug>", "task": "<what to do, self-contained>", '
        '"deadline_minutes": 30} or null}\n'
        "Route every instruction to the agent that owns the work with assign (its task restates the "
        "instruction). For a question, answer it in reply_md if you can with your read tools, else "
        "assign it. You cannot change limits or approve trades: for those, say so and assign nothing."
    )


@dataclass
class RoutingReviewer:
    """Validates the routing JSON; asks again once (``routing_retries``) on an invalid answer and
    turns the accepted one into the run's final War Room message."""

    message: OwnerMessage
    guess: Classification
    accepted: Routing | None = None
    errors: list[str] = field(default_factory=list)

    async def __call__(self, text_: str, messages: list[Message], attempt: int) -> Review:
        try:
            routing = parse_routing(text_)
        except ValueError as exc:
            self.errors.append(str(exc))
            if attempt <= int(config()["routing_retries"]):
                return Review(
                    follow_up=f"That was not a valid routing answer ({exc}). Answer with the one JSON "
                    "object only, in the format given."
                )
            return Review(failure=f"invalid routing answer: {exc}")
        self.accepted = routing
        return Review(message=self.final_message(routing))

    def final_message(self, routing: Routing) -> dict[str, Any]:
        kind: Kind = routing.kind
        content = routing.reply_md.strip()
        if kind == "limit_change_request":
            content = limit_reply(self.guess.limit)
        elif kind == "approval":
            content = APPROVAL_REPLY
        elif routing.assign is not None:
            content = f"{content} → @{agent_handles()[routing.assign.agent]}"
        return {
            "kind": "text",
            "content_md": content,
            "card": {
                "type": "owner_routing",
                "classification": kind,
                "code_classification": self.guess.kind,
                "assign": routing.assign.model_dump() if routing.assign and kind not in DECISIVE else None,
            },
            "thread_id": self.message.thread_id,
            "reply_to": self.message.row_id,
        }


async def route(host: AgentHost, message: OwnerMessage, guess: Classification) -> RunResult | None:
    """The model's routing run, then the assignment it asked for (or a fallback when it failed)."""
    ctx = host.tool_context(SLUG)
    sessions = getattr(host, "sessions", None)
    enabled = list(sessions) if isinstance(sessions, Mapping) else list(agent_handles())
    reviewer = RoutingReviewer(message, guess)
    result = await host.run(
        SLUG,
        routing_prompt(message, guess, ctx.now(), enabled),
        trigger="mention",
        task="route an owner message",
        correlation_id=message.correlation_id,
        reviewer=reviewer,
        max_follow_ups=int(config()["routing_retries"]),
        thread_id=message.thread_id,
        reply_to=message.row_id,
    )
    routing = reviewer.accepted if result.status == "ok" else None
    target: str | None = None
    task = ""
    minutes = int(config()["assign_deadline_minutes"])
    if routing is not None:
        if routing.assign is not None and routing.kind not in DECISIVE:
            target = routing.assign.agent
            task = routing.assign.task
            minutes = routing.assign.deadline_minutes or minutes
    elif guess.kind == "instruction" and len(guess.agents) == 1:
        target, task = guess.agents[0], f"Owner instruction: {_restate(message.content, 900)}"
        post(
            host,
            ctx,
            message,
            f"Routing fell back to code ({result.status}); forwarded to @{agent_handles()[target]}.",
        )
    else:
        post(
            host,
            ctx,
            message,
            f"I could not route this ({result.status}). Please @mention the agent you want, "
            "e.g. @Strategist.",
        )
    if target is not None:
        deadline = ctx.now() + timedelta(minutes=minutes)
        try:
            host.publish(
                WARROOM_STREAM,
                assign_envelope(
                    ctx, target, task, deadline, thread_id=message.thread_id, reply_to=message.row_id
                ),
            )
        except ToolError as exc:
            log.warning("orchestrator: assign to %s refused: %s", target, exc)
    return result


async def handle_owner_message(host: AgentHost, message: OwnerMessage) -> Classification:
    """Steps 1-2 now (classify, acknowledge, record the latency); returns the classification. The
    caller starts :func:`route` for a non-decisive kind."""
    ctx = host.tool_context(SLUG)
    guess = classify(message.content)
    if guess.kind == "limit_change_request":
        content, card_type = limit_reply(guess.limit), "owner_limit_answer"
    elif guess.kind == "approval":
        content, card_type = APPROVAL_REPLY, "owner_approval_answer"
    else:
        content, card_type = ack_text(guess.kind, message.content), "owner_ack"
    at = ctx.now()
    latency = (at - message.ts).total_seconds()
    card: dict[str, Any] = {
        "type": card_type,
        "classification": guess.kind,
        "ack_latency_seconds": round(max(0.0, latency), 3),
    }
    if guess.limit is not None:
        card["setting"] = guess.limit.setting
        card["settings_section"] = guess.limit.section
    post(host, ctx, message, content, card)
    try:
        await asyncio.to_thread(record_score, ctx, at, ack_latency=latency)
    except Exception:  # a score write must never cost the owner the answer
        log.exception("orchestrator: could not record the acknowledgement latency")
    return guess


__all__ = [
    "OwnerMessage",
    "Routing",
    "RoutingReviewer",
    "config",
    "handle_owner_message",
    "iso",
    "parse_routing",
    "post",
    "record_score",
    "route",
]
