"""The brief §7.10 tool access matrix — the only place an agent's ``allowed_tools`` is built.

``MATRIX`` maps each agent to the tools it may call, by server. How each §7.10 cell is read, using the
agent's own tool list in §7.x where the cell is a summary:

* okx_market: "all read" = every okx_market tool; "candles, tickers" (News), "mark, spec"
  (Strategist) and "mark, candles" (PM) name the tools.
* atlas_data: "read" and "all read" are both every atlas_data tool — every tool on that server is
  read-only (settings come only as a numbers-only snapshot), so the two wordings grant the same set.
* news: "all" (Scout, News) = every news tool except web_search, which belongs to the "web search"
  column; "calendar" (Historian) = the economic and earnings calendars; "sentiment" (Derivatives) =
  search_news (sentiment fields, §7.5); "read" (Strategist, PM, and Devil's Advocate — ATL-100,
  security round 9, low: a stress-tester needs to read evidence, not fetch arbitrary live pages) =
  every news tool except fetch_article (article bodies) and web_search.
* web search: built-in WebSearch plus news.web_search, for Market Scout and News Intelligence only.
  WebFetch is granted to no agent (ATL-100): it has no domain policy and no stored-article path;
  news.fetch_article is the one guarded live-fetch tool, checked against
  agents.web.allow_domains/deny_domains. WebSearch itself is gated the same way by a PreToolUse
  hook (ATL-121, security round 11, medium): ``allowed_tools`` grants it as a whole-tool entry,
  which the SDK auto-approves before a ``can_use_tool`` callback would even run, so the domain
  policy is enforced in a hook instead — see ``_web_search_hooks`` below.
* history: "all" = every history tool; otherwise the named tools.
* memory: "read" = read_journal + read_notebook; "notebook" (Orchestrator) = read_notebook +
  append_notebook; "read + notebook" (Historian) = read + append_notebook; "read journal" (Technical)
  = read_journal; "read + write journal" (PM) = read + write_journal, plus append_notebook, which
  §7.8 lists for the PM. memory.get_my_scores goes to every agent: the shared preamble tells each
  agent to read its own scores at the start of every cycle.
* warroom: every agent gets post_message (the preamble's injection alert), reply and mention.
  submit_proposal only for Strategist and Portfolio Manager (amend/close kinds only — the tool
  enforces that); cast_vote only for the five voters (§7.9); request_human_input only for the
  Orchestrator (§7.9).
* orchestration: the Orchestrator's §7.9 set, Orchestrator only.

No agent gets a trading, transfer, withdrawal, leverage or settings-write tool, and none exists in
the registry. Every Claude Code built-in tool is in ``disallowed_tools`` except the web pair for the
two agents that are allowed it; ``tools`` (the CLI's base built-in set) is that web pair or nothing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

from atlas_shared.defaults import DEFAULTS
from claude_agent_sdk import ClaudeAgentOptions, HookContext, HookInput, HookJSONOutput, HookMatcher
from claude_agent_sdk.types import HookEvent

from atlas_agents.registry import REGISTRY, UnknownAgentError
from atlas_agents.tools import build_mcp_servers, fq_name, registered_tools
from atlas_agents.tools.catalogue import CATALOGUE
from atlas_agents.tools.framework import ToolContext

WEB_TOOLS: Final = ("WebSearch",)
"""The CLI's built-in web tool(s) for Market Scout and News Intelligence (brief §6.1). WebFetch is
deliberately absent (ATL-100, security round 9): news.fetch_article is the agents' only live-fetch
path, and it is checked against a domain allow/deny list; an unguarded WebFetch would not be."""

# Every built-in tool of the Claude Code CLI the agents run on. All are disallowed for every agent
# (WebSearch excepted for the two web agents; WebFetch is disallowed for everyone — ATL-100).
# Unknown names are harmless to the CLI.
BUILTIN_TOOLS: Final = (
    "Agent",
    "Task",
    "TaskOutput",
    "TaskStop",
    "Bash",
    "BashOutput",
    "KillShell",
    "KillBash",
    "Monitor",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    "Glob",
    "Grep",
    "LS",
    "LSP",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "TodoRead",
    "ExitPlanMode",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitWorktree",
    "Skill",
    "SlashCommand",
    "ToolSearch",
    "AskUserQuestion",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "CronCreate",
    "CronDelete",
    "CronList",
    "RemoteTrigger",
    "PushNotification",
    "SendMessage",
)


def _all(server: str, *, without: tuple[str, ...] = ()) -> tuple[str, ...]:
    return tuple(name for name in CATALOGUE[server] if name not in without)


_OKX_ALL: Final = _all("okx_market")
_ATLAS_DATA: Final = _all("atlas_data")
_NEWS_ALL: Final = _all("news", without=("web_search",))
_NEWS_READ: Final = _all("news", without=("web_search", "fetch_article"))
_NEWS_CALENDAR: Final = ("get_economic_calendar", "get_earnings_calendar")
_HISTORY_ALL: Final = _all("history")
_MEMORY_READ: Final = ("read_journal", "read_notebook", "get_my_scores")
_WARROOM_BASE: Final = ("post_message", "reply", "mention")

ServerGrants = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class AgentAccess:
    """One agent's row of the matrix: MCP tools by server, and whether it gets the web tools."""

    tools: ServerGrants
    web: bool = False

    def mcp_tools(self) -> tuple[str, ...]:
        names = [fq_name(server, name) for server, names in self.tools.items() for name in names]
        if self.web:
            names.append(fq_name("news", "web_search"))
        return tuple(sorted(names))


MATRIX: Final[dict[str, AgentAccess]] = {
    "orchestrator": AgentAccess(
        {
            "atlas_data": _ATLAS_DATA,
            "memory": ("read_notebook", "append_notebook", "get_my_scores"),
            "warroom": (*_WARROOM_BASE, "request_human_input"),
            "orchestration": _all("orchestration"),
        }
    ),
    "market_scout": AgentAccess(
        {
            "okx_market": _OKX_ALL,
            "atlas_data": _ATLAS_DATA,
            "news": _NEWS_ALL,
            "history": _HISTORY_ALL,
            "memory": _MEMORY_READ,
            "warroom": _WARROOM_BASE,
        },
        web=True,
    ),
    "news_intelligence": AgentAccess(
        {
            "okx_market": ("get_candles", "get_tickers"),
            "atlas_data": _ATLAS_DATA,
            "news": _NEWS_ALL,
            "history": ("event_study",),
            "memory": _MEMORY_READ,
            "warroom": (*_WARROOM_BASE, "cast_vote"),
        },
        web=True,
    ),
    "historian": AgentAccess(
        {
            "okx_market": _OKX_ALL,
            "atlas_data": _ATLAS_DATA,
            "news": _NEWS_CALENDAR,
            "history": _HISTORY_ALL,
            "memory": (*_MEMORY_READ, "append_notebook"),
            "warroom": (*_WARROOM_BASE, "cast_vote"),
        }
    ),
    "technical_analyst": AgentAccess(
        {
            "okx_market": _OKX_ALL,
            "atlas_data": _ATLAS_DATA,
            "history": ("compute_stats", "find_analogs", "volatility_forecast"),
            "memory": ("read_journal", "get_my_scores"),
            "warroom": (*_WARROOM_BASE, "cast_vote"),
        }
    ),
    "derivatives_sentiment": AgentAccess(
        {
            "okx_market": _OKX_ALL,
            "atlas_data": _ATLAS_DATA,
            "news": ("search_news",),
            "history": ("event_study", "compute_stats"),
            "memory": _MEMORY_READ,
            "warroom": (*_WARROOM_BASE, "cast_vote"),
        }
    ),
    "strategist": AgentAccess(
        {
            "okx_market": ("get_mark_price", "get_instrument_spec"),
            "atlas_data": _ATLAS_DATA,
            "news": _NEWS_READ,
            "history": ("backtest_rule", "find_analogs"),
            "memory": _MEMORY_READ,
            "warroom": (*_WARROOM_BASE, "submit_proposal"),
        }
    ),
    "devils_advocate": AgentAccess(
        {
            "okx_market": _OKX_ALL,
            "atlas_data": _ATLAS_DATA,
            # _NEWS_READ, not _NEWS_ALL: no fetch_article (ATL-100, security round 9, low) — a
            # stress-tester reads the team's stored evidence, it does not need its own live fetch.
            "news": _NEWS_READ,
            "history": _HISTORY_ALL,
            "memory": _MEMORY_READ,
            "warroom": (*_WARROOM_BASE, "cast_vote"),
        }
    ),
    "portfolio_manager": AgentAccess(
        {
            "okx_market": ("get_mark_price", "get_candles"),
            "atlas_data": _ATLAS_DATA,
            "news": _NEWS_READ,
            "history": ("volatility_forecast",),
            "memory": (*_MEMORY_READ, "write_journal", "append_notebook"),
            "warroom": (*_WARROOM_BASE, "submit_proposal"),
        }
    ),
}


def _access(agent: str) -> AgentAccess:
    try:
        return MATRIX[agent]
    except KeyError:
        raise UnknownAgentError(agent) from None


def allowed_tools(agent: str) -> list[str]:
    """Every tool ``agent`` may call: fully qualified MCP names, plus WebSearch for the two web
    agents. Raises if the matrix names a tool that is not registered."""
    access = _access(agent)
    mcp = access.mcp_tools()
    missing = sorted(set(mcp) - set(registered_tools()))
    if missing:
        raise LookupError(f"access matrix for [{agent}] names unregistered tools: {missing}")
    return [*mcp, *(WEB_TOOLS if access.web else ())]


def builtin_tools(agent: str) -> list[str]:
    """The CLI's base built-in tool set (``options.tools``) for ``agent``."""
    return list(WEB_TOOLS) if _access(agent).web else []


def disallowed_tools(agent: str) -> list[str]:
    """Every built-in except the ones ``agent`` is allowed."""
    keep = set(builtin_tools(agent))
    return [name for name in BUILTIN_TOOLS if name not in keep]


def _effective_allow_domains(ctx: ToolContext) -> tuple[str, ...]:
    """The ``agents.web.allow_domains`` in force for ``ctx`` right now: the owner's settings as
    the supervisor last merged them (``ToolContext.web_policy``, ATL-114) when the context
    carries one, else config/defaults.yaml — read fresh on every call, not once at session build,
    so a change in Settings -> Agents takes effect on the very next WebSearch call."""
    if ctx.web_policy is not None:
        allow, _deny = ctx.web_policy()
        return tuple(allow)
    conf = DEFAULTS["agents"]["web"]
    return tuple(conf["allow_domains"])


def _web_search_hook(
    ctx: ToolContext,
) -> Callable[[HookInput, str | None, HookContext], Awaitable[HookJSONOutput]]:
    """A PreToolUse hook for the built-in WebSearch tool (ATL-121, security round 11, medium:
    enabled web agents could search and fetch with no domain policy at all, and an injected news
    alert could trigger it). ``allowed_tools`` grants WebSearch as a whole-tool entry, which the
    SDK auto-approves before a ``can_use_tool`` callback is even consulted (its own documented
    shadowing rule) — a hook is the one gate that still runs on every call. Injects
    ``allowed_domains`` from the same effective ``agents.web.allow_domains`` fetch_article is
    checked against, and denies the call outright when that list is empty (which now means "no
    host is allowed", not "any" — see ``news.domain_allowed``)."""

    async def hook(input_data: HookInput, _tool_use_id: str | None, _context: HookContext) -> HookJSONOutput:
        if input_data.get("tool_name") != "WebSearch":
            return {}
        allow = _effective_allow_domains(ctx)
        if not allow:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "agents.web.allow_domains is empty; no web host is allowed",
                }
            }
        raw_input = cast("dict[str, Any]", input_data.get("tool_input", {}))
        tool_input = {**raw_input, "allowed_domains": list(allow)}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": tool_input,
            }
        }

    return hook


def _web_search_hooks(ctx: ToolContext) -> dict[HookEvent, list[HookMatcher]]:
    """``options.hooks`` for a web-enabled agent: :func:`_web_search_hook` on every WebSearch call."""
    return {"PreToolUse": [HookMatcher(matcher="WebSearch", hooks=[_web_search_hook(ctx)])]}


def _merge_hooks(
    base: Mapping[HookEvent, list[HookMatcher]] | None,
    extra: Mapping[HookEvent, list[HookMatcher]] | None,
) -> dict[HookEvent, list[HookMatcher]] | None:
    """``base`` (whatever a caller already set) with ``extra``'s matchers appended per event."""
    if extra is None:
        return dict(base) if base is not None else None
    merged: dict[HookEvent, list[HookMatcher]] = {
        event: list(matchers) for event, matchers in (base or {}).items()
    }
    for event, matchers in extra.items():
        merged[event] = [*merged.get(event, []), *matchers]
    return merged


def build_options(agent: str, ctx: ToolContext, base: ClaudeAgentOptions | None = None) -> ClaudeAgentOptions:
    """``base`` (or fresh options) with this agent's tools wired: allowed/disallowed tools, the
    built-in base set, and in-process MCP servers holding only the agent's allowed tools.
    Permission mode is ``dontAsk`` so anything not pre-approved is denied, and only these MCP
    servers are loaded (``strict_mcp_config``). A web-enabled agent also gets the WebSearch
    PreToolUse hook (ATL-121, see ``_web_search_hooks``)."""
    if ctx.agent != agent:
        raise ValueError(f"ToolContext is for [{ctx.agent}], not [{agent}]")
    allowed = allowed_tools(agent)
    options = base or ClaudeAgentOptions()
    web_hooks = _web_search_hooks(ctx) if _access(agent).web else None
    return dataclasses.replace(
        options,
        allowed_tools=allowed,
        disallowed_tools=disallowed_tools(agent),
        tools=builtin_tools(agent),
        mcp_servers=build_mcp_servers(ctx, allowed),
        strict_mcp_config=True,
        permission_mode="dontAsk",
        hooks=_merge_hooks(options.hooks, web_hooks),
    )


def locked_options(base: ClaudeAgentOptions | None = None) -> ClaudeAgentOptions:
    """Options with no tool at all: no MCP server, no built-in, every built-in disallowed. Used for
    a session whose slug is not in the matrix (test agents), so an unknown agent fails closed."""
    return dataclasses.replace(
        base or ClaudeAgentOptions(),
        allowed_tools=[],
        disallowed_tools=list(BUILTIN_TOOLS),
        tools=[],
        mcp_servers={},
        strict_mcp_config=True,
        permission_mode="dontAsk",
    )


def options_for(agent: str, ctx: ToolContext, base: ClaudeAgentOptions | None = None) -> ClaudeAgentOptions:
    """``build_options`` for an agent in the matrix, ``locked_options`` for any other slug."""
    return build_options(agent, ctx, base) if agent in MATRIX else locked_options(base)


def _check_roster() -> None:
    if set(MATRIX) != set(REGISTRY):
        raise RuntimeError(f"access matrix agents {sorted(MATRIX)} differ from the roster {sorted(REGISTRY)}")


_check_roster()
