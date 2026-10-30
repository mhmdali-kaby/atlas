"""Live ``warroom.thinking`` events while an agent run streams, and the run's final War Room
message (brief §6.1 "Streaming", ATL-88).

A :class:`ThinkingPublisher` observes one AgentSession run (``AgentSession(observer_factory=...)``).
For every SDK message it sees it queues a segment:

* ``thinking`` / ``text``: partial-message deltas (``StreamEvent`` content_block_delta with
  ``thinking_delta`` / ``text_delta``) or whole ``ThinkingBlock`` / ``TextBlock``s. A whole block
  that repeats what was already streamed since the previous AssistantMessage is skipped.
* ``tool_use``: a ``ToolUseBlock`` (tool name and its input, cut to ``tool_input_max_chars``).
* ``tool_result``: a ``ToolResultBlock`` (output cut to ``tool_output_max_chars``).

Segments are published on ``atlas.warroom`` as ``warroom.thinking`` envelopes, at most
``agents.streaming.max_events_per_second`` per run: events are spaced at least 1/max apart, and
deltas of the same type that arrive in between are coalesced into one event. Whatever is still
queued when the run ends is flushed (still spaced), then one ``warroom.message`` is published that
carries the whole record: ``reasoning_md`` (all thinking, in order), ``tool_calls`` (name, input,
``as_of`` read from the tool result, truncated output), tokens and the notional cost. The record is
complete without the stream.

Publishing never fails a run: a publish error is logged and dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal

from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import Message, StreamEvent

from atlas_agents import events
from atlas_agents.session import RunObserver, RunResult

log = logging.getLogger(__name__)

WARROOM_STREAM: Final = "atlas.warroom"
THINKING_EVENT: Final = "warroom.thinking"
MESSAGE_EVENT: Final = "warroom.message"
NO_MESSAGE_TEXT: Final = "Run finished without a message."
ELLIPSIS: Final = "…"

DeltaType = Literal["thinking", "text", "tool_use", "tool_result"]
_TEXT_TYPES: Final = ("thinking", "text")


def streaming_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["streaming"]
    return config


def _cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: max(limit - 1, 0)] + ELLIPSIS, True


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _result_text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = [str(item.get("text", "")) for item in content if isinstance(item, Mapping)]
    return "\n".join(p for p in parts if p)


def _as_of(output: str) -> str | None:
    """The ``as_of`` of an Atlas tool result ({as_of, source, data}), when the output is one."""
    try:
        parsed = json.loads(output)
    except ValueError:
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("as_of"), str):
        return str(parsed["as_of"])
    return None


@dataclass
class _Segment:
    delta_type: DeltaType
    parts: list[str] = field(default_factory=list)
    tool: str | None = None
    tool_use_id: str | None = None
    tool_input: Any = None
    is_error: bool | None = None


@dataclass
class ToolCallRecord:
    id: str
    name: str
    input: Any
    input_truncated: bool = False
    output: str | None = None
    output_truncated: bool = False
    is_error: bool | None = None
    as_of: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "input": self.input,
            "input_truncated": self.input_truncated,
            "as_of": self.as_of,
            "output": self.output,
            "output_truncated": self.output_truncated,
            "is_error": self.is_error,
        }


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class ThinkingPublisher:
    """Publishes one run's ``warroom.thinking`` stream and its final ``warroom.message``."""

    def __init__(
        self,
        *,
        agent: str,
        env: str,
        run_id: uuid.UUID,
        publish: events.Publisher = events.bus_publisher,
        correlation_id: str | None = None,
        config: Mapping[str, Any] | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        settings = config if config is not None else streaming_config()
        self.agent = agent
        self.env = env
        self.run_id = run_id
        self.correlation_id = correlation_id
        self._publish = publish
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._interval = 1.0 / float(settings["max_events_per_second"])
        self._text_max = int(settings["event_text_max_chars"])
        self._input_max = int(settings["tool_input_max_chars"])
        self._output_max = int(settings["tool_output_max_chars"])
        self._content_max = int(DEFAULTS["warroom"]["content_max_chars"])
        self.seq = 0
        self._queue: list[_Segment] = []
        self._last_emit: float | None = None
        self._streamed: set[str] = set()
        self._reasoning: list[str] = []
        self._thinking_open = False
        self._tool_calls: dict[str, ToolCallRecord] = {}
        self.final: EventEnvelope | None = None

    # -- the record ------------------------------------------------------------------------------

    @property
    def reasoning_md(self) -> str:
        return "".join(self._reasoning)

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [call.as_json() for call in self._tool_calls.values()]

    def _add_thinking(self, text: str, *, delta: bool) -> None:
        if not text:
            return
        if self._reasoning and not (delta and self._thinking_open):
            self._reasoning.append("\n\n")
        self._reasoning.append(text)
        self._thinking_open = delta

    # -- observing -------------------------------------------------------------------------------

    async def on_message(self, message: Message) -> None:
        if isinstance(message, StreamEvent):
            self._on_stream_event(message.event)
        elif isinstance(message, AssistantMessage):
            streamed, self._streamed = self._streamed, set()
            for block in message.content:
                if isinstance(block, ThinkingBlock):
                    if "thinking" not in streamed:
                        self._add_thinking(block.thinking, delta=False)
                        self._queue_text("thinking", block.thinking)
                elif isinstance(block, TextBlock):
                    self._thinking_open = False
                    if "text" not in streamed:
                        self._queue_text("text", block.text)
                elif isinstance(block, ToolUseBlock):
                    self._thinking_open = False
                    self._on_tool_use(block)
        elif isinstance(message, UserMessage) and not isinstance(message.content, str):
            for item in message.content:
                if isinstance(item, ToolResultBlock):
                    self._on_tool_result(item)
        await self._drain(final=False)

    def _on_stream_event(self, event: Mapping[str, Any]) -> None:
        kind = event.get("type")
        if kind == "content_block_stop":
            self._thinking_open = False
            return
        if kind != "content_block_delta":
            return
        delta = event.get("delta") or {}
        if delta.get("type") == "thinking_delta":
            text = str(delta.get("thinking") or "")
            self._streamed.add("thinking")
            self._add_thinking(text, delta=True)
            self._queue_text("thinking", text)
        elif delta.get("type") == "text_delta":
            self._streamed.add("text")
            self._thinking_open = False
            self._queue_text("text", str(delta.get("text") or ""))

    def _queue_text(self, delta_type: DeltaType, text: str) -> None:
        if not text:
            return
        if self._queue and self._queue[-1].delta_type == delta_type:
            self._queue[-1].parts.append(text)
        else:
            self._queue.append(_Segment(delta_type=delta_type, parts=[text]))

    def _on_tool_use(self, block: ToolUseBlock) -> None:
        raw = _json(block.input)
        cut, truncated = _cut(raw, self._input_max)
        stored: Any = block.input if not truncated else cut
        self._tool_calls[block.id] = ToolCallRecord(
            id=block.id, name=block.name, input=stored, input_truncated=truncated
        )
        self._queue.append(
            _Segment(delta_type="tool_use", tool=block.name, tool_use_id=block.id, tool_input=stored)
        )

    def _on_tool_result(self, block: ToolResultBlock) -> None:
        output = _result_text(block.content)
        cut, truncated = _cut(output, self._output_max)
        call = self._tool_calls.get(block.tool_use_id)
        if call is None:
            call = ToolCallRecord(id=block.tool_use_id, name="unknown", input=None)
            self._tool_calls[block.tool_use_id] = call
        call.output = cut
        call.output_truncated = truncated
        call.is_error = block.is_error
        call.as_of = _as_of(output)
        self._queue.append(
            _Segment(
                delta_type="tool_result",
                parts=[cut],
                tool=call.name,
                tool_use_id=block.tool_use_id,
                is_error=block.is_error,
            )
        )

    # -- publishing ------------------------------------------------------------------------------

    async def _drain(self, *, final: bool) -> None:
        while self._queue:
            now = self._clock()
            if self._last_emit is not None:
                wait = self._last_emit + self._interval - now
                if wait > 0:
                    if not final:
                        return
                    await self._sleep(wait)
                    continue
            self._emit(self._queue.pop(0))
            self._last_emit = now

    def _emit(self, segment: _Segment) -> None:
        self.seq += 1
        text: str | None = None
        output: str | None = None
        truncated = False
        joined = "".join(segment.parts)
        if segment.delta_type in _TEXT_TYPES:
            text, truncated = _cut(joined, self._text_max)
        elif segment.delta_type == "tool_result":
            output = joined
        payload = {
            "run_id": str(self.run_id),
            "agent": self.agent,
            "seq": self.seq,
            "delta_type": segment.delta_type,
            "text": text,
            "tool": segment.tool,
            "tool_use_id": segment.tool_use_id,
            "input": segment.tool_input if segment.delta_type == "tool_use" else None,
            "output": output,
            "is_error": segment.is_error,
            "truncated": truncated,
        }
        self._send(
            EventEnvelope(
                type=THINKING_EVENT,
                ts=self._wall_clock(),
                env=self.env,
                source=f"agent:{self.agent}",
                correlation_id=self.correlation_id,
                payload=payload,
            )
        )

    def _send(self, envelope: EventEnvelope) -> None:
        try:
            self._publish(WARROOM_STREAM, envelope)
        except Exception:  # the stream is best effort; Postgres gets the final message's record
            log.warning("agent %s: could not publish %s", self.agent, envelope.type, exc_info=True)

    async def flush(self) -> None:
        await self._drain(final=True)

    def final_envelope(
        self, *, content_md: str, tokens_in: int, tokens_out: int, cost_usd: Decimal | None
    ) -> EventEnvelope:
        content, _ = _cut(content_md, self._content_max)
        cost = None if cost_usd is None else str(cost_usd.quantize(Decimal("0.000001"), ROUND_HALF_UP))
        return EventEnvelope(
            type=MESSAGE_EVENT,
            ts=self._wall_clock(),
            env=self.env,
            source=f"agent:{self.agent}",
            correlation_id=self.correlation_id,
            payload={
                "thread": {"kind": "main", "key": "main"},
                "author_type": "agent",
                "author": self.agent,
                "kind": "text",
                "content_md": content,
                "card": None,
                "reply_to": None,
                "mentions": [],
                "reasoning_md": self.reasoning_md or None,
                "tool_calls": self.tool_calls,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost,
            },
        )

    async def on_finish(self, result: RunResult) -> None:
        """Flush the queued events, then publish the final message (unless the run produced
        nothing at all: no text, no thinking, no tool call)."""
        await self.flush()
        text = result.text.strip()
        if not text and not self._reasoning and not self._tool_calls:
            return
        self.final = self.final_envelope(
            content_md=text or NO_MESSAGE_TEXT,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd_notional,
        )
        self._send(self.final)


def observer_factory(
    *, agent: str, env: str, publish: events.Publisher = events.bus_publisher
) -> Callable[[uuid.UUID, str | None], RunObserver]:
    """For ``AgentSession(observer_factory=...)``: one ThinkingPublisher per run."""

    def make(run_id: uuid.UUID, correlation_id: str | None) -> RunObserver:
        return ThinkingPublisher(
            agent=agent, env=env, run_id=run_id, publish=publish, correlation_id=correlation_id
        )

    return make
