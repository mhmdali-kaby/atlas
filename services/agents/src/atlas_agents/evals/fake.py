"""The ``--fake`` client: replays a case's scripted answer, but runs every scripted tool call for real.

Text and the final result come from the script; a ``tool`` step is sent to the real registered tool
handler, bound to the case's replay ToolContext, exactly as the CLI would route it. So the tool
outputs a fake case collects are what the tools return for the snapshot at ``replay_as_of`` — which
is what lets a pytest prove replay isolation without the real CLI. A tool outside the agent's
§7.10 allow-list gets the error the CLI would give and is not run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import Message

from atlas_agents import testing
from atlas_agents.evals.case import FakeStep
from atlas_agents.tools import ToolContext, fq_name, get_tool
from atlas_agents.tools.framework import make_handler


def full_tool_name(name: str) -> str:
    """``okx_market.get_candles`` -> ``mcp__okx_market__get_candles``; full names pass through."""
    if name.startswith("mcp__"):
        return name
    server, _, tool = name.partition(".")
    if not tool:
        raise ValueError(f"tool step [{name}] must be server.name or mcp__server__name")
    return fq_name(server, tool)


class EvalFakeClient:
    """Implements the ClaudeClient protocol of atlas_agents.session for one scripted query."""

    def __init__(self, steps: Sequence[FakeStep], ctx: ToolContext, options: ClaudeAgentOptions) -> None:
        self._steps = list(steps)
        self._ctx = ctx
        self.options = options
        self.queries: list[str] = []

    async def connect(self, prompt: str | None = None) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def set_model(self, model: str | None = None) -> None:
        return None

    async def query(self, prompt: str, session_id: str = "default") -> None:
        if self.queries:
            raise testing.ScriptExhaustedError("an eval case scripts exactly one query")
        self.queries.append(prompt)

    async def _call(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        if name not in set(self.options.allowed_tools):
            return [{"type": "text", "text": f"Tool {name} is not allowed for this agent"}], True
        result = await make_handler(get_tool(name), self._ctx)(args)
        return result["content"], bool(result.get("is_error"))

    async def receive_response(self) -> AsyncIterator[Message]:
        finished = False
        for index, step in enumerate(self._steps):
            if step.text is not None:
                yield AssistantMessage(content=[TextBlock(text=step.text)], model="fake")
            elif step.tool is not None:
                name = full_tool_name(step.tool)
                use_id = f"toolu_eval_{index}"
                yield AssistantMessage(
                    content=[ToolUseBlock(id=use_id, name=name, input=step.input)], model="fake"
                )
                content, is_error = await self._call(name, step.input)
                yield UserMessage(
                    content=[ToolResultBlock(tool_use_id=use_id, content=content, is_error=is_error)]
                )
            elif step.result is not None:
                yield testing.result(**step.result)
                finished = True
                break
        if not finished:
            yield testing.result(turns=max(1, sum(1 for s in self._steps if s.tool is not None)))
