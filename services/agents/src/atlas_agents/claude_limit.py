"""The Claude plan usage limit, shared by every agent (config: agents.claude_limit).

The limit belongs to the owner's Claude plan, not to one agent, so one ``ClaudeLimit`` gate is shared
by every session of the supervisor:

- A run that sees the limit (a rejected RateLimitEvent, a 429, "usage limit" text) trips the gate.
  While it is tripped every session refuses runs without calling Claude (status ``rate_limited``).
- A run that gets no answer for ``run_timeout_seconds`` ends as a timeout and marks the gate
  *suspect*: the supervisor probes at once, and only a probe that hits the limit trips the gate.
- The supervisor announces each transition once on ``atlas.system`` as ``system.claude_limit``
  (``state`` reached|cleared), which atlas:bridge turns into an owner notification, and posts a War
  Room line.
- While tripped, the supervisor sends a one-word probe on the routine model every ``probe_minutes``
  (not before a known reset time). The first probe Claude answers clears the gate and the team resumes.

``recover_interrupted_runs`` closes runs a stopped agents process left ``running`` and sets their
agents back to ``idle``, so a dead run never shows as "thinking" forever.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from atlas_shared.db import AgentRun, AgentState
from atlas_shared.defaults import DEFAULTS
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, RateLimitEvent, ResultMessage
from sqlalchemy import Engine, update

log = logging.getLogger(__name__)

CLAUDE_LIMIT: Final = "system.claude_limit"
PROBE_PROMPT: Final = "Reply with the single word OK."
INTERRUPTED: Final = "interrupted: the agents process stopped during this run"


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["agents"]["claude_limit"]
    return cfg


@dataclass
class ClaudeLimit:
    """Whether the owner's Claude plan is at its usage limit, and when to probe next."""

    since: datetime | None = None
    resets_at: datetime | None = None
    next_probe_at: datetime | None = None
    suspect: bool = False
    announced: bool = False
    recovered: bool = False
    """A run got an answer after a known reset time passed: the supervisor announces the resume."""

    @property
    def limited(self) -> bool:
        return self.since is not None

    def refuses(self, now: datetime) -> bool:
        """Refuse runs while limited, until a known reset time passes (then runs may try again)."""
        return self.limited and (self.resets_at is None or now < self.resets_at)

    def answered(self) -> None:
        if self.limited:
            self.recovered = True

    def trip(self, now: datetime, resets_at: datetime | None) -> None:
        if self.since is None:
            self.since = now
            self.announced = False
            self.next_probe_at = now + timedelta(minutes=float(config()["probe_minutes"]))
        if resets_at is not None:
            self.resets_at = resets_at
        self.suspect = False

    def mark_suspect(self) -> None:
        if not self.limited:
            self.suspect = True

    def probe_due(self, now: datetime) -> bool:
        if self.suspect:
            return True
        if not self.limited or self.next_probe_at is None:
            return False
        return now >= self.next_probe_at

    def after_failed_probe(self, now: datetime, resets_at: datetime | None) -> None:
        self.trip(now, resets_at)
        self.next_probe_at = now + timedelta(minutes=float(config()["probe_minutes"]))

    def clear(self) -> None:
        self.since = self.resets_at = self.next_probe_at = None
        self.suspect = self.announced = self.recovered = False


@dataclass(frozen=True)
class ProbeResult:
    limited: bool
    resets_at: datetime | None = None
    error: str | None = None


async def probe(
    client_factory: Callable[[ClaudeAgentOptions], Any],
    options: ClaudeAgentOptions,
    *,
    limit_seconds: float | None = None,
) -> ProbeResult:
    """Ask Claude for one word. ``limited`` is True when the limit answers, or nothing answers in time."""
    seconds = float(config()["probe_timeout_seconds"]) if limit_seconds is None else limit_seconds
    opts = dataclasses.replace(options, max_turns=1, tools=[], setting_sources=[], skills=[], mcp_servers={})
    client = client_factory(opts)
    resets_at: datetime | None = None
    try:
        async with asyncio.timeout(seconds):
            await client.connect()
            await client.query(PROBE_PROMPT)
            async for message in client.receive_response():
                if isinstance(message, RateLimitEvent) and message.rate_limit_info.status == "rejected":
                    at = message.rate_limit_info.resets_at
                    resets_at = datetime.fromtimestamp(at, UTC) if at else None
                    return ProbeResult(limited=True, resets_at=resets_at)
                if isinstance(message, AssistantMessage) and message.error == "rate_limit":
                    return ProbeResult(limited=True)
                if isinstance(message, ResultMessage):
                    if message.is_error:
                        return ProbeResult(limited=True, error=message.result or message.subtype)
                    return ProbeResult(limited=False)
        return ProbeResult(limited=True, error="no result")
    except TimeoutError:
        return ProbeResult(limited=True, error="timeout")
    except Exception as exc:  # a dead CLI counts as "not back yet"
        return ProbeResult(limited=True, error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            log.debug("claude limit probe: disconnect failed", exc_info=True)


def recover_interrupted_runs(engine: Engine, now: datetime | None = None) -> int:
    """Close every ``running`` agent run (the process that ran it is gone) and set ``thinking``
    agents back to ``idle``. Returns how many runs were closed."""
    at = now or datetime.now(UTC)
    with engine.begin() as conn:
        closed = conn.execute(
            update(AgentRun)
            .where(AgentRun.status == "running")
            .values(status="error", ended_at=at, error=INTERRUPTED, updated_at=at)
        ).rowcount
        conn.execute(
            update(AgentState)
            .where(AgentState.state == "thinking")
            .values(state="idle", current_task=None, state_since=at, updated_at=at)
        )
    if closed:
        log.info("agents: closed %d run(s) left running by a stopped process", closed)
    return int(closed or 0)


def announce(
    publish: Callable[[str, Any], object], *, env: str, state: str, gate: ClaudeLimit, at: datetime, zone: Any
) -> None:
    """Publish ``system.claude_limit`` (the owner notification) and a War Room line for one transition."""
    import uuid

    from atlas_shared.events import EventEnvelope

    resets = gate.resets_at
    payload: dict[str, Any] = {
        "state": state,
        "since": gate.since.isoformat() if gate.since else None,
        "resets_at": resets.isoformat() if resets else None,
        "probe_minutes": int(float(config()["probe_minutes"])),
    }
    if state == "reached":
        when = f" around {resets.astimezone(zone):%H:%M}" if resets else ""
        text = (
            "**Claude usage limit reached.** The agents are paused and will not research or propose trades. "
            f"Atlas checks every {payload['probe_minutes']} minutes and resumes them "
            f"when the limit resets{when}. "
            "Open positions keep their stop-loss on OKX."
        )
    else:
        text = "**Claude is answering again.** The agents have resumed their work."
    key = f"{env}:{state}:{at.isoformat()}"
    system = EventEnvelope(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"claude-limit:{key}"),
        type=CLAUDE_LIMIT,
        env=env,
        source="system:agents",
        payload=payload,
    )
    message = EventEnvelope(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"claude-limit-warroom:{key}"),
        type="warroom.message",
        env=env,
        source="system:agents",
        payload={
            "thread": {"kind": "main", "key": "main"},
            "author_type": "system",
            "author": "system:agents",
            "kind": "system_alert",
            "content_md": text,
            "card": {"code": f"claude_limit_{state}", **payload},
            "reply_to": None,
            "mentions": [],
            "reasoning_md": None,
            "tool_calls": None,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        },
    )
    for stream, envelope in (("atlas.system", system), ("atlas.warroom", message)):
        try:
            publish(stream, envelope)
        except Exception:  # Redis down must not stop the agents loop
            log.warning("agents: could not publish the Claude limit %s event", state, exc_info=True)
