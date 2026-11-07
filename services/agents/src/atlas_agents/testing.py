"""A fake ClaudeSDKClient that replays scripted SDK messages (decision #245: no test calls the CLI).

A script is a list of runs; each ``query()`` consumes the next run. A run is a list of steps, each
either a real SDK message object (AssistantMessage, UserMessage, RateLimitEvent, ResultMessage, ...)
or an exception, which is raised when the replay reaches it. ``receive_response()`` stops after a
ResultMessage, like the real client. The builders below produce the common steps::

    client = FakeClaudeClient([
        [assistant_text("Regime: trend"), result(tokens_in=1200, tokens_out=300, turns=2)],
        [rate_limit_rejected(resets_at=1_793_000_000)],
    ])
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import Message, StreamEvent

Step = Message | BaseException
ScriptedRun = Sequence[Step]


class ScriptExhaustedError(AssertionError):
    """The code under test sent more queries than the script has runs."""


class FakeClaudeClient:
    """Implements the slice of ClaudeSDKClient that AgentSession uses."""

    def __init__(self, script: Sequence[ScriptedRun] = (), options: ClaudeAgentOptions | None = None):
        self.options = options
        self._script: list[ScriptedRun] = list(script)
        self._current: ScriptedRun | None = None
        self.queries: list[str] = []
        self.models: list[str | None] = []
        self.connects = 0
        self.disconnects = 0

    def add_run(self, run: ScriptedRun) -> None:
        self._script.append(run)

    @property
    def remaining(self) -> int:
        return len(self._script)

    async def connect(self, prompt: str | None = None) -> None:
        self.connects += 1

    async def disconnect(self) -> None:
        self.disconnects += 1

    async def set_model(self, model: str | None = None) -> None:
        self.models.append(model)

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries.append(prompt)
        if not self._script:
            raise ScriptExhaustedError(f"no scripted run left for query #{len(self.queries)}")
        self._current = self._script.pop(0)

    async def receive_response(self) -> AsyncIterator[Message]:
        run, self._current = self._current or (), None
        for step in run:
            if isinstance(step, BaseException):
                raise step
            yield step
            if isinstance(step, ResultMessage):
                return


def factory(client: FakeClaudeClient) -> Any:
    """A client factory for AgentSession that hands out ``client`` and records the options."""

    def make(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client.options = options
        return client

    return make


def _usage(tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0) -> dict[str, Any]:
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }


def assistant_text(text: str, *, model: str = "opus", message_id: str | None = None) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model=model, message_id=message_id)


def thinking(text: str, *, model: str = "opus") -> AssistantMessage:
    """A whole thinking block (what the CLI sends without partial messages)."""
    return AssistantMessage(content=[ThinkingBlock(thinking=text, signature="sig")], model=model)


def _delta(delta: dict[str, Any], index: int) -> StreamEvent:
    return StreamEvent(
        uuid=f"se-{index}",
        session_id="fake-session",
        event={"type": "content_block_delta", "index": index, "delta": delta},
    )


def thinking_delta(text: str, *, index: int = 0) -> StreamEvent:
    """A partial-message thinking delta (``include_partial_messages=True``)."""
    return _delta({"type": "thinking_delta", "thinking": text}, index)


def text_delta(text: str, *, index: int = 1) -> StreamEvent:
    return _delta({"type": "text_delta", "text": text}, index)


def block_stop(*, index: int = 0) -> StreamEvent:
    return StreamEvent(
        uuid=f"se-stop-{index}",
        session_id="fake-session",
        event={"type": "content_block_stop", "index": index},
    )


def tool_use(name: str, tool_input: dict[str, Any], *, tool_use_id: str = "toolu_1") -> AssistantMessage:
    return AssistantMessage(content=[ToolUseBlock(id=tool_use_id, name=name, input=tool_input)], model="opus")


def tool_result(content: str, *, tool_use_id: str = "toolu_1", is_error: bool = False) -> UserMessage:
    return UserMessage(content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)])


def result(
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    turns: int = 1,
    cost_usd: float | None = None,
    is_error: bool = False,
    api_error_status: int | None = None,
    text: str | None = None,
    errors: list[str] | None = None,
    subtype: str = "success",
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=turns,
        session_id="fake-session",
        total_cost_usd=cost_usd,
        usage=_usage(tokens_in, tokens_out, cache_read, cache_write),
        result=text,
        errors=errors,
        api_error_status=api_error_status,
    )


def rate_limit_rejected(*, resets_at: int | None = None, limit_type: str = "five_hour") -> RateLimitEvent:
    """The CLI's event when the subscription usage limit has been hit."""
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(
            status="rejected",
            resets_at=resets_at,
            rate_limit_type=limit_type,  # type: ignore[arg-type]
        ),
        uuid="rl-1",
        session_id="fake-session",
    )


def rate_limit_result(*, text: str = "Claude usage limit reached.") -> ResultMessage:
    """A terminal error result for a 429, as the CLI reports a limit hit mid-run."""
    return result(is_error=True, api_error_status=429, text=text, turns=1)
