"""The brief §7.10 access matrix, asserted cell by cell for all nine agents (ATL-84).

EXPECTED is written out by hand from §7.10 (with the per-agent tool lists of §7.4-§7.9 where a cell
is a summary; the reading of each cell is documented in atlas_agents.access). It deliberately does
not reuse access.py's constants, so a change there that drifts from the brief fails here.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from atlas_agents import access
from atlas_agents.registry import REGISTRY, UnknownAgentError
from atlas_agents.supervisor import Supervisor
from atlas_agents.testing import FakeClaudeClient, factory
from atlas_agents.tools import ToolContext, registered_tools
from claude_agent_sdk import ClaudeAgentOptions
from sqlalchemy import Engine

OKX_ALL = {
    "list_instruments",
    "get_tickers",
    "get_candles",
    "get_orderbook",
    "get_funding",
    "get_open_interest",
    "get_long_short_ratio",
    "get_taker_volume",
    "get_mark_price",
    "get_instrument_spec",
    "get_ta_table",  # ATL-135: the Technical Analyst's pre-computed table
    "get_positioning_features",  # ATL-136: the Derivatives & Sentiment features
}
ATLAS_DATA_ALL = {
    "get_account_summary",
    "get_positions",
    "get_open_orders",
    "get_settings_snapshot",
    "get_today_budget_left",
    "get_watchlist",
    "get_correlations",
}
NEWS_ALL = {
    "search_news",
    "get_story_cluster",
    "get_economic_calendar",
    "get_earnings_calendar",
    "get_okx_announcements",
    "fetch_article",
}
NEWS_READ = NEWS_ALL - {"fetch_article"}
HISTORY_ALL = {
    "compute_stats",
    "detect_regime",
    "find_analogs",
    "event_study",
    "seasonality",
    "backtest_rule",
    "volatility_forecast",
    "drawdown_profile",
    "cross_asset_lead_lag",
}
MEMORY_READ = {"read_journal", "read_notebook", "get_my_scores"}
WARROOM_BASE = {"post_message", "reply", "mention"}
ORCHESTRATION = {"start_cycle", "assign", "open_vote", "close_vote", "set_agent_state", "summarise_thread"}

# agent -> {server: tool names}; "web" is the web-search column.
EXPECTED: dict[str, dict[str, set[str]]] = {
    "orchestrator": {
        "atlas_data": ATLAS_DATA_ALL,
        "memory": {"read_notebook", "append_notebook", "get_my_scores"},
        "warroom": WARROOM_BASE | {"request_human_input"},
        "orchestration": ORCHESTRATION,
    },
    "market_scout": {
        "okx_market": OKX_ALL,
        "atlas_data": ATLAS_DATA_ALL,
        "news": NEWS_ALL,
        "web": {"yes"},
        "history": HISTORY_ALL,
        "memory": MEMORY_READ,
        "warroom": WARROOM_BASE,
    },
    "news_intelligence": {
        "okx_market": {"get_candles", "get_tickers"},
        "atlas_data": ATLAS_DATA_ALL,
        "news": NEWS_ALL,
        "web": {"yes"},
        "history": {"event_study"},
        "memory": MEMORY_READ,
        "warroom": WARROOM_BASE | {"cast_vote"},
    },
    "historian": {
        "okx_market": OKX_ALL,
        "atlas_data": ATLAS_DATA_ALL,
        "news": {"get_economic_calendar", "get_earnings_calendar"},
        "history": HISTORY_ALL,
        "memory": MEMORY_READ | {"append_notebook"},
        "warroom": WARROOM_BASE | {"cast_vote"},
    },
    "technical_analyst": {
        "okx_market": OKX_ALL,
        "atlas_data": ATLAS_DATA_ALL,
        "history": {"compute_stats", "find_analogs", "volatility_forecast"},
        "memory": {"read_journal", "get_my_scores"},
        "warroom": WARROOM_BASE | {"cast_vote"},
    },
    "derivatives_sentiment": {
        "okx_market": OKX_ALL,
        "atlas_data": ATLAS_DATA_ALL,
        "news": {"search_news"},
        "history": {"event_study", "compute_stats"},
        "memory": MEMORY_READ,
        "warroom": WARROOM_BASE | {"cast_vote"},
    },
    "strategist": {
        "okx_market": {"get_mark_price", "get_instrument_spec"},
        "atlas_data": ATLAS_DATA_ALL,
        "news": NEWS_READ,
        "history": {"backtest_rule", "find_analogs"},
        "memory": MEMORY_READ,
        "warroom": WARROOM_BASE | {"submit_proposal"},
    },
    "devils_advocate": {
        "okx_market": OKX_ALL,
        "atlas_data": ATLAS_DATA_ALL,
        "news": NEWS_READ,
        "history": HISTORY_ALL,
        "memory": MEMORY_READ,
        "warroom": WARROOM_BASE | {"cast_vote"},
    },
    "portfolio_manager": {
        "okx_market": {"get_mark_price", "get_candles"},
        "atlas_data": ATLAS_DATA_ALL,
        "news": NEWS_READ,
        "history": {"volatility_forecast"},
        "memory": MEMORY_READ | {"write_journal", "append_notebook"},
        "warroom": WARROOM_BASE | {"submit_proposal"},
    },
}
SERVERS = ("okx_market", "atlas_data", "news", "history", "memory", "warroom", "orchestration")
WEB_AGENTS = {"market_scout", "news_intelligence"}
VOTERS = {"news_intelligence", "historian", "technical_analyst", "derivatives_sentiment", "devils_advocate"}
FORBIDDEN = re.compile(
    r"order|cancel|amend|transfer|withdraw|set_leverage|settings?_?(write|set|update|save)"
)
# The two read-only tools whose brief §6.2 names contain "order": they read the open-order list and
# the order book from Postgres and never write. Exact names, so any new "order" tool fails the test.
READ_ONLY_ORDER_TOOLS = {"mcp__atlas_data__get_open_orders", "mcp__okx_market__get_orderbook"}
CORE_BUILTINS = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "NotebookEdit",
    "Agent",
    "Task",
)


def _granted(agent: str) -> dict[str, set[str]]:
    by_server: dict[str, set[str]] = {}
    for name in access.allowed_tools(agent):
        if name in ("WebSearch", "WebFetch"):
            by_server.setdefault("builtin", set()).add(name)
            continue
        _mcp, server, tool = name.split("__")
        by_server.setdefault(server, set()).add(tool)
    return by_server


def test_matrix_covers_exactly_the_nine_agents() -> None:
    assert set(EXPECTED) == set(REGISTRY) == set(access.MATRIX)
    assert len(EXPECTED) == 9


@pytest.mark.parametrize("agent", sorted(EXPECTED))
@pytest.mark.parametrize("server", SERVERS)
def test_each_cell_matches_the_brief(agent: str, server: str) -> None:
    expected = set(EXPECTED[agent].get(server, set()))
    if server == "news" and "web" in EXPECTED[agent]:
        expected.add("web_search")
    assert _granted(agent).get(server, set()) == expected


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_allowed_set_is_exactly_the_row(agent: str) -> None:
    expected = {f"mcp__{s}__{t}" for s, tools in EXPECTED[agent].items() if s != "web" for t in tools}
    if agent in WEB_AGENTS:
        expected |= {"mcp__news__web_search", "WebSearch"}
    allowed = access.allowed_tools(agent)
    assert len(allowed) == len(set(allowed))
    assert set(allowed) == expected


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_web_tools_only_for_scout_and_news(agent: str) -> None:
    allowed = set(access.allowed_tools(agent))
    disallowed = set(access.disallowed_tools(agent))
    # ATL-100 (security round 9, low): WebFetch has no domain policy and is granted to nobody,
    # including the two web agents — news.fetch_article is their guarded live-fetch path instead.
    assert "WebFetch" not in allowed
    assert "WebFetch" in disallowed
    web = {"WebSearch", "mcp__news__web_search"}
    if agent in WEB_AGENTS:
        assert web <= allowed
        assert "WebSearch" not in disallowed
        assert access.builtin_tools(agent) == ["WebSearch"]
    else:
        assert not web & allowed
        assert "WebSearch" in disallowed
        assert access.builtin_tools(agent) == []


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_web_search_is_either_absent_or_gated_by_a_pretooluse_hook(agent: str, engine: Engine) -> None:
    """ATL-121 (security round 11, medium): an ``allowed_tools`` entry that grants a whole tool
    (``"WebSearch"``, no specifier) is auto-approved by the SDK before a ``can_use_tool`` callback
    would even run, so a callback cannot gate it; a PreToolUse hook is the one thing that still
    runs on every call. Every agent either has no WebSearch at all, or has a hook wired for it."""
    options = access.build_options(agent, _ctx(agent, engine))
    matchers = (options.hooks or {}).get("PreToolUse", [])
    hooked = any(m.matcher == "WebSearch" and m.hooks for m in matchers)
    if "WebSearch" in access.allowed_tools(agent):
        assert hooked
    else:
        assert not hooked


def _web_search_hook(options: ClaudeAgentOptions) -> Any:
    (matcher,) = (options.hooks or {})["PreToolUse"]
    (hook,) = matcher.hooks
    return hook


async def test_web_search_hook_denies_the_call_when_the_allow_list_is_empty(engine: Engine) -> None:
    ctx = dataclasses.replace(_ctx("market_scout", engine), web_policy=lambda: ((), ()))
    options = access.build_options("market_scout", ctx)
    hook = _web_search_hook(options)
    result = await hook({"tool_name": "WebSearch", "tool_input": {"query": "x"}}, "id", {"signal": None})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_web_search_hook_injects_allowed_domains_from_the_effective_list(engine: Engine) -> None:
    ctx = dataclasses.replace(
        _ctx("news_intelligence", engine), web_policy=lambda: (("coindesk.com", "finnhub.io"), ())
    )
    options = access.build_options("news_intelligence", ctx)
    hook = _web_search_hook(options)
    result = await hook({"tool_name": "WebSearch", "tool_input": {"query": "gold"}}, "id", {"signal": None})
    out = result["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"] == {"query": "gold", "allowed_domains": ["coindesk.com", "finnhub.io"]}


async def test_web_search_hook_ignores_calls_to_other_tools(engine: Engine) -> None:
    ctx = dataclasses.replace(_ctx("market_scout", engine), web_policy=lambda: ((), ()))
    options = access.build_options("market_scout", ctx)
    hook = _web_search_hook(options)
    assert await hook({"tool_name": "Bash", "tool_input": {}}, "id", {"signal": None}) == {}


def test_web_search_hook_falls_back_to_config_defaults_with_no_web_policy(engine: Engine) -> None:
    """A context built outside the supervisor (no ``web_policy``) uses config/defaults.yaml's
    allow list, same as ``news.domain_allowed``."""
    from atlas_shared.defaults import DEFAULTS

    ctx = _ctx("market_scout", engine)
    assert ctx.web_policy is None
    assert access._effective_allow_domains(ctx) == tuple(DEFAULTS["agents"]["web"]["allow_domains"])


async def test_web_search_hook_excludes_a_host_the_deny_list_covers(engine: Engine) -> None:
    """ATL-123 (security round 12, low): the hook used to read only the allow half of the
    policy, so a host under ``agents.web.deny_domains`` still reached WebSearch. ``bad.example.com``
    is under the deny entry, so either the whole call is refused or it is dropped from
    ``allowed_domains`` — never passed through."""
    ctx = dataclasses.replace(
        _ctx("market_scout", engine),
        web_policy=lambda: (("example.com", "bad.example.com"), ("bad.example.com",)),
    )
    options = access.build_options("market_scout", ctx)
    hook = _web_search_hook(options)
    result = await hook({"tool_name": "WebSearch", "tool_input": {"query": "x"}}, "id", {"signal": None})
    out = result["hookSpecificOutput"]
    if out["permissionDecision"] == "allow":
        assert "bad.example.com" not in out["updatedInput"]["allowed_domains"]
    else:
        assert out["permissionDecision"] == "deny"


async def test_web_search_hook_denies_when_allow_equals_deny(engine: Engine) -> None:
    ctx = dataclasses.replace(
        _ctx("market_scout", engine),
        web_policy=lambda: (("example.com",), ("example.com",)),
    )
    options = access.build_options("market_scout", ctx)
    hook = _web_search_hook(options)
    result = await hook({"tool_name": "WebSearch", "tool_input": {"query": "x"}}, "id", {"signal": None})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_web_search_hook_overwrites_model_supplied_domain_lists(engine: Engine) -> None:
    ctx = dataclasses.replace(_ctx("news_intelligence", engine), web_policy=lambda: (("coindesk.com",), ()))
    options = access.build_options("news_intelligence", ctx)
    hook = _web_search_hook(options)
    result = await hook(
        {
            "tool_name": "WebSearch",
            "tool_input": {
                "query": "gold",
                "allowed_domains": ["evil.example"],
                "blocked_domains": ["coindesk.com"],
            },
        },
        "id",
        {"signal": None},
    )
    out = result["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"] == {"query": "gold", "allowed_domains": ["coindesk.com"]}


def test_devils_advocate_has_no_live_fetch() -> None:
    """ATL-100 (security round 9, low): the Devil's Advocate read every news tool including
    fetch_article, so it could reach any public URL with no domain policy. It now gets the same
    read-only news row as Strategist/PM — no fetch_article, no web_search, no WebFetch."""
    allowed = set(access.allowed_tools("devils_advocate"))
    assert "mcp__news__fetch_article" not in allowed
    assert "WebFetch" not in allowed
    assert "WebSearch" not in allowed
    assert "mcp__news__web_search" not in allowed


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_proposals_votes_and_human_input(agent: str) -> None:
    allowed = set(access.allowed_tools(agent))
    assert ("mcp__warroom__submit_proposal" in allowed) == (agent in {"strategist", "portfolio_manager"})
    assert ("mcp__warroom__cast_vote" in allowed) == (agent in VOTERS)
    assert ("mcp__warroom__request_human_input" in allowed) == (agent == "orchestrator")
    assert any(t.startswith("mcp__orchestration__") for t in allowed) == (agent == "orchestrator")


def test_technical_analyst_row_cell_by_cell() -> None:
    """ATL-135 AC1: brief §7.10's Technical Analyst row, written out cell by cell."""
    allowed = set(access.allowed_tools("technical_analyst"))
    by_server: dict[str, set[str]] = {}
    for name in allowed:
        _mcp, server, tool = name.split("__", 2)
        by_server.setdefault(server, set()).add(tool)
    assert by_server["okx_market"] == OKX_ALL  # all read, including get_ta_table
    assert "get_ta_table" in by_server["okx_market"]
    assert by_server["atlas_data"] == ATLAS_DATA_ALL  # read
    assert "news" not in by_server  # no news
    assert not any(t in allowed for t in ("WebSearch", "WebFetch", "mcp__news__web_search"))  # no web
    assert by_server["history"] == {
        "compute_stats",
        "find_analogs",
        "volatility_forecast",
    }  # stats, analogs, vol
    assert by_server["memory"] == {"read_journal", "get_my_scores"}  # read journal (+ own scores)
    assert by_server["warroom"] == {"post_message", "reply", "mention", "cast_vote"}  # cast_vote yes
    assert "mcp__warroom__submit_proposal" not in allowed  # no submit_proposal
    assert set(by_server) == {"okx_market", "atlas_data", "history", "memory", "warroom"}


def test_derivatives_sentiment_row_cell_by_cell() -> None:
    """ATL-136 AC1: brief §7.10's Derivatives & Sentiment row, written out cell by cell."""
    allowed = set(access.allowed_tools("derivatives_sentiment"))
    by_server: dict[str, set[str]] = {}
    for name in allowed:
        _mcp, server, tool = name.split("__", 2)
        by_server.setdefault(server, set()).add(tool)
    assert by_server["okx_market"] == OKX_ALL  # all read, including get_positioning_features
    assert "get_positioning_features" in by_server["okx_market"]
    assert by_server["atlas_data"] == ATLAS_DATA_ALL  # read
    assert by_server["news"] == {"search_news"}  # sentiment: search_news only
    assert not any(t in allowed for t in ("WebSearch", "WebFetch", "mcp__news__web_search"))  # no web
    assert by_server["history"] == {"event_study", "compute_stats"}  # event_study, stats
    assert by_server["memory"] == {"read_journal", "read_notebook", "get_my_scores"}  # read (+ own scores)
    assert by_server["warroom"] == {"post_message", "reply", "mention", "cast_vote"}  # cast_vote yes
    assert "mcp__warroom__submit_proposal" not in allowed  # no submit_proposal
    assert "mcp__warroom__request_human_input" not in allowed
    assert set(by_server) == {"okx_market", "atlas_data", "news", "history", "memory", "warroom"}


def test_orchestrator_cannot_vote() -> None:
    assert "mcp__warroom__cast_vote" not in access.allowed_tools("orchestrator")


def test_no_registered_tool_can_trade_transfer_or_write_settings() -> None:
    names = set(registered_tools())
    assert names, "the catalogue registers every §6.2 tool"
    hits = {n for n in names if FORBIDDEN.search(n.lower())}
    assert hits == READ_ONLY_ORDER_TOOLS
    for agent in EXPECTED:
        for name in access.allowed_tools(agent):
            assert name in READ_ONLY_ORDER_TOOLS or not FORBIDDEN.search(name.lower()), (agent, name)


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_builtins_are_disallowed(agent: str) -> None:
    disallowed = access.disallowed_tools(agent)
    for name in CORE_BUILTINS:
        assert name in disallowed
    assert not set(disallowed) & set(access.allowed_tools(agent))


def test_every_matrix_tool_is_registered_and_every_catalogue_tool_is_reachable() -> None:
    granted = {name for agent in EXPECTED for name in access.allowed_tools(agent)}
    registered = set(registered_tools())
    assert granted - {"WebSearch", "WebFetch"} <= registered
    assert registered <= granted


def test_unknown_agent_is_refused() -> None:
    with pytest.raises(UnknownAgentError):
        access.allowed_tools("trader")


def _ctx(agent: str, engine: Engine) -> ToolContext:
    from sqlalchemy.orm import sessionmaker

    return ToolContext(env="demo", session_factory=sessionmaker(bind=engine), agent=agent)


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_build_options_wires_only_the_rows_servers(agent: str, engine: Engine) -> None:
    options = access.build_options(agent, _ctx(agent, engine), ClaudeAgentOptions(system_prompt="x"))
    assert options.system_prompt == "x"
    assert options.allowed_tools == access.allowed_tools(agent)
    assert options.disallowed_tools == access.disallowed_tools(agent)
    assert options.tools == access.builtin_tools(agent)
    assert options.permission_mode == "dontAsk"
    assert options.strict_mcp_config is True
    assert isinstance(options.mcp_servers, dict)
    expected_servers = {s for s in EXPECTED[agent] if s != "web"}
    assert set(options.mcp_servers) == expected_servers
    for config in options.mcp_servers.values():
        assert config["type"] == "sdk"


def test_build_options_refuses_a_context_for_another_agent(engine: Engine) -> None:
    with pytest.raises(ValueError, match="ToolContext is for"):
        access.build_options("strategist", _ctx("historian", engine))


def test_unknown_slug_fails_closed(engine: Engine) -> None:
    options = access.options_for("test_agent", _ctx("test_agent", engine))
    assert options.allowed_tools == []
    assert options.mcp_servers == {}
    assert options.tools == []
    assert "Bash" in options.disallowed_tools


def test_supervisor_sessions_get_matrix_options(engine: Engine) -> None:
    spec = REGISTRY["historian"]
    sup = Supervisor(
        env="demo",
        engine=engine,
        registry={"historian": dataclasses.replace(spec, enabled=True)},
        client_factory=factory(FakeClaudeClient([])),
        replay_as_of=datetime(2026, 10, 1, tzinfo=UTC),
    )
    options = sup.sessions["historian"].options
    assert options.allowed_tools == access.allowed_tools("historian")
    assert options.tools == []  # the session keeps the matrix's empty built-in set
    assert set(options.disallowed_tools) >= set(CORE_BUILTINS)
    assert isinstance(options.mcp_servers, dict)
    assert "okx_market" in options.mcp_servers and "orchestration" not in options.mcp_servers
