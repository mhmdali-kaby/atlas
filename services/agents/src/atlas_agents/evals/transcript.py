"""A run's messages as the :class:`~atlas_agents.evals.properties.Transcript` the properties score."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from claude_agent_sdk import AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from atlas_agents.evals import properties as props
from atlas_agents.session import RunResult


def tool_output(content: Any) -> list[dict[str, Any]]:
    """A ToolResultBlock's content as JSON objects (the tools' {as_of, source, data} envelopes)."""
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts.extend(str(item.get("text", "")) for item in content if isinstance(item, Mapping))
    out: list[dict[str, Any]] = []
    for text in texts:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        out.append(parsed if isinstance(parsed, dict) else {"text": text})
    return out


def transcript(result: RunResult, prompt: str = "") -> props.Transcript:
    calls: list[tuple[str, dict[str, Any]]] = []
    outputs: list[dict[str, Any]] = []
    texts: list[str] = []
    for message in result.messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    calls.append((block.name, dict(block.input)))
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    outputs.extend(tool_output(block.content))
    # What the War Room shows is the run's text plus anything posted with post_message/reply.
    posted = [str(args.get("content_md", "")) for name, args in calls if name in props.POST_TOOLS]
    text = "\n\n".join(t for t in [*texts, *posted] if t)
    return props.Transcript(
        status=result.status,
        text=text,
        card=props.card_from(text, calls),
        tool_calls=calls,
        tool_outputs=outputs,
        error=result.error,
        prompt=prompt,
    )


__all__ = ["tool_output", "transcript"]
