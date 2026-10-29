"""The tool framework (ATL-84): result envelope, as_of refusal, replay bound, trimming, input
validation, catalogue stubs and the SDK MCP wiring."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from atlas_agents.tools import ToolContext, ToolError, ToolInput, ToolResult, build_mcp_servers, fq_name
from atlas_agents.tools import framework as fw
from atlas_agents.tools.catalogue import CATALOGUE
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, MetaData, Table, select

AS_OF = datetime(2026, 10, 29, 7, 30, tzinfo=UTC)
CONFIG = {"max_result_tokens": 4000, "chars_per_token": 4, "trim_rows": 50}


class RowsIn(ToolInput):
    n: int = 10
    width: int = 10


class Row(BaseModel):
    i: int
    text: str


class RowsOut(BaseModel):
    rows: list[Row]
    note: str = ""


def _no_session() -> Any:
    raise AssertionError("tools under test do not touch the DB")


def _ctx(replay_as_of: datetime | None = None) -> ToolContext:
    return ToolContext(env="demo", session_factory=_no_session, agent="historian", replay_as_of=replay_as_of)


@pytest.fixture
def scratch_registry() -> Iterator[None]:
    saved = dict(fw._REGISTRY)
    yield
    fw._REGISTRY.clear()
    fw._REGISTRY.update(saved)


def _tool(fn: Any, name: str = "rows", server: str = "history") -> fw.ToolDef:
    return fw.ToolDef(
        server=server, name=name, description="test rows", input_model=RowsIn, output_model=RowsOut, fn=fn
    )


async def _rows(ctx: ToolContext, args: RowsIn) -> ToolResult:
    rows = [{"i": i, "text": "x" * args.width} for i in range(args.n)]
    return ToolResult(data={"rows": rows}, as_of=AS_OF, source="test.rows")


def _body(result: dict[str, Any]) -> dict[str, Any]:
    assert len(result["content"]) == 1
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    return body


async def _call(tool: fw.ToolDef, args: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
    return await fw.make_handler(tool, ctx or _ctx(), CONFIG)(args)


async def test_result_is_wrapped_with_as_of_source_and_data() -> None:
    result = await _call(_tool(_rows), {"n": 2})
    assert not result.get("is_error")
    assert _body(result) == {
        "as_of": "2026-10-29T07:30:00+00:00",
        "source": "test.rows",
        "truncated": False,
        "data": {"rows": [{"i": 0, "text": "x" * 10}, {"i": 1, "text": "x" * 10}], "note": ""},
    }


async def test_50k_chars_of_rows_are_trimmed_to_the_row_limit() -> None:
    # 500 rows of ~100 chars: ~50,000 chars (~12,500 tokens) > 4,000.
    raw = await _rows(_ctx(), RowsIn(n=500, width=88))
    assert len(json.dumps(raw.data)) >= 50_000
    result = await _call(_tool(_rows), {"n": 500, "width": 88})
    body = _body(result)
    assert not result.get("is_error")
    assert body["truncated"] is True
    assert len(body["data"]["rows"]) == CONFIG["trim_rows"]
    assert [r["i"] for r in body["data"]["rows"]] == list(range(50))  # first rows kept, in order
    assert body["omitted_rows"] == {"data.rows": 450}
    assert len(result["content"][0]["text"]) / 4 <= CONFIG["max_result_tokens"]


async def test_rows_that_still_do_not_fit_are_halved_until_they_do() -> None:
    result = await _call(_tool(_rows), {"n": 200, "width": 1000})
    body = _body(result)
    assert body["truncated"] is True
    assert len(result["content"][0]["text"]) / 4 <= CONFIG["max_result_tokens"]
    kept = len(body["data"]["rows"])
    assert 1 <= kept < 50
    assert body["omitted_rows"]["data.rows"] == 200 - kept


async def test_a_result_that_cannot_fit_is_an_error() -> None:
    async def huge(ctx: ToolContext, args: RowsIn) -> ToolResult:
        return ToolResult(data={"rows": [], "note": "y" * 40_000}, as_of=AS_OF, source="t")

    result = await _call(_tool(huge), {})
    assert result["is_error"] is True
    assert _body(result)["error"] == "result_too_large"


async def test_small_results_are_not_marked_truncated() -> None:
    body = _body(await _call(_tool(_rows), {"n": 50}))
    assert body["truncated"] is False
    assert "omitted_rows" not in body


@pytest.mark.parametrize("as_of", [None, datetime(2026, 10, 29, 7, 30)])  # noqa: DTZ001 - naive on purpose
async def test_a_result_without_an_aware_as_of_is_refused(as_of: datetime | None) -> None:
    async def no_as_of(ctx: ToolContext, args: RowsIn) -> ToolResult:
        return ToolResult(data={"rows": []}, as_of=as_of, source="t")

    result = await _call(_tool(no_as_of), {})
    assert result["is_error"] is True
    body = _body(result)
    assert body["error"] == "missing_as_of"
    assert "data" not in body


async def test_a_result_newer_than_the_replay_cutoff_is_refused() -> None:
    result = await _call(_tool(_rows), {}, _ctx(replay_as_of=AS_OF - timedelta(minutes=1)))
    assert result["is_error"] is True
    assert _body(result)["error"] == "look_ahead"
    ok = await _call(_tool(_rows), {}, _ctx(replay_as_of=AS_OF))
    assert not ok.get("is_error")


async def test_output_that_fails_its_model_is_refused() -> None:
    async def bad(ctx: ToolContext, args: RowsIn) -> ToolResult:
        return ToolResult(data={"rows": [{"i": "not a number"}]}, as_of=AS_OF, source="t")

    assert _body(await _call(_tool(bad), {}))["error"] == "invalid_result"


async def test_a_non_toolresult_return_is_refused() -> None:
    async def raw(ctx: ToolContext, args: RowsIn) -> Any:
        return {"rows": []}

    assert _body(await _call(_tool(raw), {}))["error"] == "invalid_result"


async def test_invalid_and_unknown_arguments_are_error_results() -> None:
    assert _body(await _call(_tool(_rows), {"n": "many"}))["error"] == "invalid_input"
    assert _body(await _call(_tool(_rows), {"bogus": 1}))["error"] == "invalid_input"


async def test_tool_errors_and_crashes_become_error_results() -> None:
    async def refuses(ctx: ToolContext, args: RowsIn) -> ToolResult:
        raise ToolError("stale_data", "candles are 3 bars late")

    async def crashes(ctx: ToolContext, args: RowsIn) -> ToolResult:
        raise RuntimeError("secret detail")

    assert _body(await _call(_tool(refuses), {})) == {
        "error": "stale_data",
        "message": "candles are 3 bars late",
    }
    crashed = _body(await _call(_tool(crashes), {}))
    assert crashed["error"] == "tool_failed"
    assert "secret detail" not in crashed["message"]


# Catalogue tools with a real implementation; each has its own tests
# (ATL-85: okx_market, atlas_data; ATL-86: news, memory).
IMPLEMENTED = {
    *(("okx_market", n) for n in CATALOGUE["okx_market"]),
    *(("atlas_data", n) for n in CATALOGUE["atlas_data"]),
    *(("news", n) for n in CATALOGUE["news"] if n != "web_search"),
    *(("memory", n) for n in CATALOGUE["memory"]),
}


def test_implemented_tools_are_exactly_the_built_ones() -> None:
    built = {(t.server, t.name) for t in fw.registered_tools().values() if t.implemented}
    catalogue = {(s, n) for s, tools in CATALOGUE.items() for n in tools}
    assert built & catalogue == IMPLEMENTED


def test_implemented_tools_keep_their_catalogue_input_model() -> None:
    for server, name in IMPLEMENTED:
        tool = fw.get_tool(fq_name(server, name))
        assert tool.input_model is CATALOGUE[server][name], tool.fq_name


@pytest.mark.parametrize(
    ("server", "name"),
    [(s, n) for s, tools in CATALOGUE.items() for n in tools if (s, n) not in IMPLEMENTED],
)
async def test_every_catalogue_tool_is_a_not_implemented_stub(server: str, name: str) -> None:
    tool = fw.get_tool(fq_name(server, name))
    assert tool.implemented is False
    schema = tool.input_schema()
    assert schema["type"] == "object"
    result = await fw.make_handler(tool, _ctx(), CONFIG)(_minimal_args(schema))
    assert result["is_error"] is True
    assert _body(result)["error"] == "not_implemented"


def _minimal_args(schema: dict[str, Any]) -> dict[str, Any]:
    samples: dict[str, Any] = {
        "string": "x",
        "integer": 30,
        "number": 1.0,
        "object": {},
        "boolean": True,
    }
    args: dict[str, Any] = {}
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        prop = props[key]
        if "enum" in prop:
            args[key] = prop["enum"][0]
        elif prop.get("format") == "date-time":
            args[key] = "2026-10-29T00:00:00Z"
        elif prop.get("format") == "date":
            args[key] = "2026-10-29"
        elif prop.get("type") == "array":
            args[key] = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
        elif key == "confidence":
            args[key] = 0.5
        else:
            args[key] = samples[prop.get("type", "string")]
    return args


def test_a_real_tool_replaces_its_stub_and_a_stub_never_replaces_a_real_tool(scratch_registry: None) -> None:
    name = fq_name("history", "compute_stats")
    real = fw.atlas_tool("history", "compute_stats", RowsIn, RowsOut)

    @real
    async def compute_stats(ctx: ToolContext, args: RowsIn) -> ToolResult:
        """Real stats."""
        return await _rows(ctx, args)

    assert fw.get_tool(name).implemented is True
    from atlas_agents.tools.catalogue import register_catalogue

    register_catalogue()
    assert fw.get_tool(name).implemented is True
    with pytest.raises(ValueError, match="registered twice"):
        fw.register(_tool(_rows, name="compute_stats"))


def test_registration_rules(scratch_registry: None) -> None:
    with pytest.raises(ValueError, match="unknown tool server"):
        fw.register(_tool(_rows, server="okx_trade"))
    with pytest.raises(ValueError, match="snake_case"):
        fw.register(_tool(_rows, name="Place-Order"))
    with pytest.raises(ValueError, match="description"):
        fw.register(fw.ToolDef("history", "nodoc", "  ", RowsIn, RowsOut, _rows))


def test_build_mcp_servers_holds_only_allowed_registered_tools() -> None:
    servers = build_mcp_servers(
        _ctx(), ["mcp__history__compute_stats", "mcp__news__search_news", "WebSearch", "mcp__nope__x"], CONFIG
    )
    assert set(servers) == {"history", "news"}
    assert all(cfg["type"] == "sdk" and cfg["name"] in {"history", "news"} for cfg in servers.values())


async def test_tools_reach_the_sdk_as_json_schema_and_bound_handlers(
    scratch_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fw.register(_tool(_rows, name="rows_probe"))
    seen: dict[str, list[Any]] = {}

    def record(name: str, version: str, tools: list[Any]) -> dict[str, Any]:
        seen[name] = tools
        return {"type": "sdk", "name": name, "instance": None}

    monkeypatch.setattr(fw, "create_sdk_mcp_server", record)
    build_mcp_servers(_ctx(), ["mcp__history__rows_probe"], CONFIG)
    [sdk] = seen["history"]
    assert sdk.name == "rows_probe" and sdk.description == "test rows"
    assert sdk.input_schema["type"] == "object"
    assert sdk.input_schema["properties"]["n"]["type"] == "integer"
    payload = _body(await sdk.handler({"n": 1}))
    assert payload["source"] == "test.rows" and payload["data"]["rows"][0]["i"] == 0


def test_bound_clause_applies_only_in_replay() -> None:
    table = Table("t", MetaData(), Column("ts", DateTime(timezone=True)))
    live = str(select(table).where(_ctx().bound(table.c.ts)).compile(compile_kwargs={"literal_binds": True}))
    assert "ts <=" not in live
    replay = select(table).where(_ctx(replay_as_of=AS_OF).bound(table.c.ts))
    assert "t.ts <= :ts_1" in str(replay)
    assert _ctx(replay_as_of=AS_OF).now() == AS_OF
    assert _ctx().replaying is False


def test_replay_as_of_must_be_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _ctx(replay_as_of=datetime(2026, 10, 1))  # noqa: DTZ001
