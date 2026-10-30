"""The agents' tool layer (brief §6.1/§6.2/§7.11, decision #245).

A tool is an async function ``fn(ctx, args) -> ToolResult`` registered with ``@atlas_tool``. The
registry turns it into an in-process SDK MCP tool whose input JSON schema comes from a pydantic
model, and whose every result reaches the model as one JSON object::

    {"as_of": "2026-10-29T07:30:00+00:00", "source": "atlas.candles", "truncated": false, "data": {...}}

The wrapper, not the tool, enforces the rules every tool must keep:

* a result without ``as_of`` (or with a naive one) is refused with an error result;
* in replay mode a result stamped after ``ctx.replay_as_of`` is refused (no look-ahead);
* ``data`` is validated against the tool's output model;
* a result estimated above ``agents.tools.max_result_tokens`` is trimmed by code and marked
  ``truncated: true`` with the rows dropped per list in ``omitted_rows``;
* invalid input and any exception become an error result the model can read; nothing raises out.

Tools read Postgres only. Every query must be bounded with ``ctx.bound(column)`` so a replayed day
cannot see data newer than ``ctx.replay_as_of``.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from atlas_shared import bus
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from claude_agent_sdk import McpServerConfig, SdkMcpTool, create_sdk_mcp_server
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import ColumnElement, true
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

SERVERS: Final = ("okx_market", "atlas_data", "news", "history", "warroom", "memory", "orchestration")
SERVER_VERSION: Final = "1.0.0"
_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")

Json = dict[str, Any]


def _tools_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["tools"]
    return config


def fq_name(server: str, name: str) -> str:
    """The fully qualified name the CLI uses for an SDK MCP tool: ``mcp__<server>__<tool>``."""
    return f"mcp__{server}__{name}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _bus_publish(stream: str, envelope: EventEnvelope) -> object:
    """The real publisher: XADD to Redis through atlas_shared.bus."""
    return bus.publish(stream, envelope)


@dataclass
class RunScope:
    """The run a session is in right now (ATL-101). Mutable and shared by every ToolContext the
    supervisor builds for one agent; it sets it under the agent's run lock (runs of one agent never
    overlap) and resets it after. ``mention_depth`` is the depth of the mention that started the
    run (0 for an owner mention, a schedule or a manual run); see :mod:`atlas_agents.mentions`."""

    correlation_id: str | None = None
    mention_depth: int = 0


@dataclass(frozen=True)
class ToolContext:
    """What every tool call gets besides its arguments. One per agent session.

    ``replay_as_of`` is None live. When it is set the tool is answering as of that instant: every
    query must be bounded with ``bound()`` and ``now()`` returns the replay instant.
    """

    env: str
    session_factory: Callable[[], Session]
    agent: str
    correlation_id: str | None = None
    replay_as_of: datetime | None = None
    clock: Callable[[], datetime] = _utcnow
    publish: Callable[[str, EventEnvelope], object] = _bus_publish
    """How a tool puts an event on the bus (e.g. the untrusted-text War Room alert). Tests pass a
    recorder or a publisher bound to the test Redis DB."""
    run: RunScope = field(default_factory=RunScope)
    """The current run (correlation id, mention depth); see :class:`RunScope`."""
    web_policy: Callable[[], tuple[Sequence[str], Sequence[str]]] | None = None
    """``() -> (allow_domains, deny_domains)``: the owner's ``agents.web`` lists as the supervisor
    last merged them from settings (ATL-114). None (a context built outside the supervisor) means
    the config/defaults.yaml lists."""

    def __post_init__(self) -> None:
        if self.replay_as_of is not None and self.replay_as_of.utcoffset() is None:
            raise ValueError("ToolContext.replay_as_of must be timezone-aware")

    @property
    def replaying(self) -> bool:
        return self.replay_as_of is not None

    def now(self) -> datetime:
        """The instant this call answers for: the replay cutoff, else the wall clock (UTC)."""
        return self.replay_as_of if self.replay_as_of is not None else self.clock()

    def bound(self, column: Any) -> ColumnElement[bool]:
        """``column <= replay_as_of`` in replay mode, else an always-true clause. Put it in every
        query's WHERE on the timestamp that says when the row became known."""
        if self.replay_as_of is None:
            return true()
        clause: ColumnElement[bool] = column <= self.replay_as_of
        return clause


class ToolInput(BaseModel):
    """Base for tool argument models: unknown arguments are an error, not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class NoInput(ToolInput):
    """For tools that take no arguments."""


@dataclass(frozen=True)
class ToolResult:
    """What a tool returns. ``data`` is an instance of (or a mapping valid for) the output model."""

    data: Any
    as_of: datetime | None
    source: str


class ToolError(Exception):
    """Raise from a tool to return an error result with a stable ``code`` the model can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


ToolFn = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDef:
    server: str
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: ToolFn
    implemented: bool = True

    @property
    def fq_name(self) -> str:
        return fq_name(self.server, self.name)

    def input_schema(self) -> Json:
        return self.input_model.model_json_schema()


_REGISTRY: dict[str, ToolDef] = {}


def register(tool: ToolDef) -> ToolDef:
    """Add ``tool``. A real tool replaces the catalogue stub of the same name; a stub never
    replaces a real tool; two real tools with one name is a programming error."""
    if tool.server not in SERVERS:
        raise ValueError(f"unknown tool server [{tool.server}]; servers are {SERVERS}")
    if not _NAME.match(tool.name):
        raise ValueError(f"tool name [{tool.name}] must be snake_case")
    if not tool.description.strip():
        raise ValueError(f"tool [{tool.fq_name}] needs a description (the function docstring)")
    existing = _REGISTRY.get(tool.fq_name)
    if existing is not None:
        if not tool.implemented:
            return existing
        if existing.implemented:
            raise ValueError(f"tool [{tool.fq_name}] is registered twice")
    _REGISTRY[tool.fq_name] = tool
    return tool


def atlas_tool(
    server: str,
    name: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
) -> Callable[[ToolFn], ToolFn]:
    """Register ``async def fn(ctx: ToolContext, args: input_model) -> ToolResult``. The function's
    docstring is the description the model reads."""

    def decorate(fn: ToolFn) -> ToolFn:
        register(
            ToolDef(
                server=server,
                name=name,
                description=(fn.__doc__ or "").strip(),
                input_model=input_model,
                output_model=output_model,
                fn=fn,
            )
        )
        return fn

    return decorate


def registered_tools() -> dict[str, ToolDef]:
    """Every registered tool by fully qualified name (a copy)."""
    return dict(_REGISTRY)


def get_tool(name: str) -> ToolDef:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"no tool [{name}] is registered") from None


# -- results ---------------------------------------------------------------------------------------


def error_result(code: str, message: str) -> Json:
    """An MCP error result the model reads as ``{"error": code, "message": ...}``."""
    body = json.dumps({"error": code, "message": message}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": body}], "is_error": True}


def _text_result(payload: Json) -> Json:
    return {"content": [{"type": "text", "text": _dumps(payload)}]}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def estimate_tokens(text: str, chars_per_token: int) -> int:
    return math.ceil(len(text) / chars_per_token)


@dataclass
class _Trim:
    omitted: dict[str, int] = field(default_factory=dict)

    def cut(self, path: str, items: list[Any], keep: int) -> None:
        dropped = len(items) - keep
        if dropped > 0:
            del items[keep:]
            self.omitted[path] = self.omitted.get(path, 0) + dropped


def _lists(node: Any, path: str = "data") -> Iterable[tuple[str, list[Any]]]:
    if isinstance(node, list):
        yield path, node
        for i, item in enumerate(node):
            yield from _lists(item, f"{path}[{i}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _lists(value, f"{path}.{key}")


def _cut_all(node: Any, path: str, rows: int, trim: _Trim) -> None:
    """Cut every list to ``rows`` rows, parents before children (so dropped rows are not visited)."""
    if isinstance(node, list):
        trim.cut(path, node, rows)
        for i, item in enumerate(node):
            _cut_all(item, f"{path}[{i}]", rows, trim)
    elif isinstance(node, dict):
        for key, value in node.items():
            _cut_all(value, f"{path}.{key}", rows, trim)


def trim_payload(
    payload: Json, *, max_tokens: int, chars_per_token: int, rows: int
) -> tuple[Json, dict[str, int]] | None:
    """Trim ``payload["data"]`` in place until the payload fits ``max_tokens``.

    First every list is cut to ``rows`` rows; then, while still too big, the longest list is halved.
    Rows are kept from the start, so tools return the most relevant rows first. Returns the payload
    and the dropped row count per list path, or None if it cannot be made to fit."""
    if estimate_tokens(_dumps(payload), chars_per_token) <= max_tokens:
        return payload, {}
    trim = _Trim()
    _cut_all(payload["data"], "data", rows, trim)
    while estimate_tokens(_dumps(payload), chars_per_token) > max_tokens:
        candidates = [(len(items), path, items) for path, items in _lists(payload["data"]) if len(items) > 1]
        if not candidates:
            return None
        _n, path, items = max(candidates, key=lambda c: c[0])
        trim.cut(path, items, len(items) // 2)
    return payload, trim.omitted


def wrap_result(tool: ToolDef, ctx: ToolContext, result: Any, config: Mapping[str, Any]) -> Json:
    """Turn what a tool returned into the MCP result the model sees (see the module docstring)."""
    if not isinstance(result, ToolResult):
        return error_result("invalid_result", f"{tool.fq_name} must return a ToolResult")
    if result.as_of is None:
        return error_result("missing_as_of", f"{tool.fq_name} returned no as_of; the result is refused")
    if result.as_of.utcoffset() is None:
        return error_result("missing_as_of", f"{tool.fq_name} returned a naive as_of; the result is refused")
    if ctx.replay_as_of is not None and result.as_of > ctx.replay_as_of:
        return error_result(
            "look_ahead",
            f"{tool.fq_name} returned data as of {result.as_of.isoformat()}, after the replay cutoff",
        )
    if not result.source.strip():
        return error_result("missing_source", f"{tool.fq_name} returned no source")
    try:
        data = tool.output_model.model_validate(result.data)
    except ValidationError as exc:
        return error_result(
            "invalid_result", f"{tool.fq_name} output failed validation: {exc.error_count()} errors"
        )
    payload: Json = {
        "as_of": result.as_of.astimezone(UTC).isoformat(),
        "source": result.source,
        "truncated": False,
        "data": data.model_dump(mode="json"),
    }
    trimmed = trim_payload(
        payload,
        max_tokens=int(config["max_result_tokens"]),
        chars_per_token=int(config["chars_per_token"]),
        rows=int(config["trim_rows"]),
    )
    if trimmed is None:
        return error_result("result_too_large", f"{tool.fq_name} result does not fit even after trimming")
    payload, omitted = trimmed
    if omitted:
        payload["truncated"] = True
        payload["omitted_rows"] = omitted
    return _text_result(payload)


def make_handler(
    tool: ToolDef, ctx: ToolContext, config: Mapping[str, Any] | None = None
) -> Callable[[Json], Awaitable[Json]]:
    """The SDK handler for ``tool`` bound to ``ctx``. Never raises."""
    settings = config if config is not None else _tools_config()

    async def handler(args: Json) -> Json:
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as exc:
            return error_result("invalid_input", _validation_summary(exc))
        try:
            result = await tool.fn(ctx, parsed)
        except ToolError as exc:
            return error_result(exc.code, exc.message)
        except Exception as exc:  # a broken tool must not take the agent session down
            log.exception("tool %s failed for agent %s", tool.fq_name, ctx.agent)
            return error_result("tool_failed", f"{tool.fq_name} failed: {type(exc).__name__}")
        return wrap_result(tool, ctx, result, settings)

    return handler


def _validation_summary(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in exc.errors()]
    return "; ".join(parts)


def sdk_tool(tool: ToolDef, ctx: ToolContext, config: Mapping[str, Any] | None = None) -> SdkMcpTool[Any]:
    return SdkMcpTool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema(),
        handler=make_handler(tool, ctx, config),
    )


def build_mcp_servers(
    ctx: ToolContext, allowed: Iterable[str], config: Mapping[str, Any] | None = None
) -> dict[str, McpServerConfig]:
    """In-process MCP servers holding only the registered tools named in ``allowed`` (fully
    qualified). Names that are not registered tools (built-ins such as WebSearch) are skipped.
    Servers with no allowed tool are left out entirely, so an agent cannot even list them."""
    by_server: dict[str, list[SdkMcpTool[Any]]] = {}
    for name in sorted(set(allowed)):
        tool = _REGISTRY.get(name)
        if tool is not None:
            by_server.setdefault(tool.server, []).append(sdk_tool(tool, ctx, config))
    return {
        server: create_sdk_mcp_server(name=server, version=SERVER_VERSION, tools=tools)
        for server, tools in by_server.items()
    }
