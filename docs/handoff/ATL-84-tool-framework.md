# ATL-84 — Agent tool framework, catalogue and the §7.10 access matrix

Package `services/agents/src/atlas_agents`: `tools/framework.py`, `tools/catalogue.py`,
`tools/untrusted.py`, `access.py`. Tests: `agents/tests/test_tools.py`, `test_access.py`,
`test_untrusted.py` (no DB, no CLI).

## The two-minute version

- **Every §6.2 tool name (plus the Orchestrator's §7.9 `orchestration` set) is already registered as
  a stub** that answers the error `not_implemented`. You *replace* a stub by registering a real tool
  with the same server and name via `@atlas_tool`. A stub never overwrites a real tool; two real
  tools with one name raise.
- **Put real tools in their own module** (e.g. `atlas_agents/tools/history.py`) and import it from
  `atlas_agents/tools/__init__.py` — registration happens at import time.
- **Return a `ToolResult(data, as_of, source)`. The wrapper refuses anything else**: no `as_of` or a
  naive one → `missing_as_of`; in replay an `as_of` later than `ctx.replay_as_of` → `look_ahead`;
  data that fails your output model → `invalid_result`; empty source → `missing_source`.
- **Bound every query with `ctx.bound(column)`** on the column that says when the row was known
  (`ts`, `published_at`/`fetched_at`, `created_at`). It is `true()` live and `column <= replay_as_of`
  in replay. Use `ctx.now()` instead of `datetime.now()` for "today" / "last 24h".
- **Order your rows most relevant first.** Over 4,000 tokens (chars/4) the wrapper cuts every list to
  `trim_rows` (50) rows, keeping the head, then halves the longest list until it fits.
- **Never put external text in a result raw.** Pass it through `untrusted(text, source)` and, if
  `.flagged`, raise a War Room alert (the tool knows the item; the framework does not post).
- **Tools never get credentials.** They read Postgres via `ctx.session_factory()`; never OKX, never
  the web (decision #245).

## Writing a tool

```python
from atlas_agents.tools import ToolContext, ToolError, ToolInput, ToolResult, atlas_tool
from atlas_agents.tools.catalogue import StatsInput     # reuse the catalogue's input model
from pydantic import BaseModel

class StatsOut(BaseModel):
    inst_id: str
    rows: list[dict[str, float]]

@atlas_tool("history", "compute_stats", StatsInput, StatsOut)
async def compute_stats(ctx: ToolContext, args: StatsInput) -> ToolResult:
    """Returns, volatility, ATR, skew ... for inst_id on bar over lookback bars."""  # model reads this
    with ctx.session_factory() as s:
        rows = s.execute(select(Candle).where(Candle.inst_id == args.inst_id, ctx.bound(Candle.ts))...)
    if not rows:
        raise ToolError("no_data", f"no {args.bar} candles for {args.inst_id}")
    return ToolResult(data=StatsOut(...), as_of=last_closed_ts, source="atlas.candles")
```

- The docstring is the description (required). Input models subclass `ToolInput`
  (`extra="forbid"`); `NoInput` for no-arg tools. The SDK receives `input_model.model_json_schema()`.
- `ToolError(code, message)` → `{"error": code, "message": message}`, `is_error: true`. Any other
  exception → `tool_failed` with only the exception type (no message, so nothing internal leaks).
- Invalid arguments → `invalid_input` with `loc: msg` per error.
- The catalogue's input models (`catalogue.py`, `CATALOGUE[server][name]`) are the contract; change
  one deliberately if your tool needs different arguments. Note `news.get_economic_calendar` /
  `get_earnings_calendar` take `from` (alias of `from_`).
- `sdk_tool()` builds the SDK tool; `make_handler(tool, ctx, config)` is the never-raising handler —
  call it directly in tests.

## What the model sees

Success (`content[0].text`, compact JSON):
```json
{"as_of":"2026-10-29T07:30:00+00:00","source":"atlas.candles","truncated":true,
 "data":{"rows":[...50 rows...]},"omitted_rows":{"data.rows":450}}
```
`truncated` is always present; `omitted_rows` (list path → rows dropped) only when trimmed. A result
that cannot fit even with every list at one row → `result_too_large`.
Error: `{"error":"missing_as_of","message":"history.x returned no as_of; the result is refused"}`
with `is_error: true`. Codes: `invalid_input`, `invalid_result`, `missing_as_of`, `missing_source`,
`look_ahead`, `result_too_large`, `tool_failed`, `not_implemented`, plus your own `ToolError` codes.

Config: `agents.tools.{max_result_tokens: 4000, chars_per_token: 4, trim_rows: 50,
injection_patterns: [...]}` in `config/defaults.yaml` (regenerate with `php artisan atlas:defaults`).

## ToolContext

```python
ToolContext(env: str, session_factory: Callable[[], Session], agent: str,
            correlation_id: str | None = None, replay_as_of: datetime | None = None,
            clock: Callable[[], datetime] = utcnow)
ctx.replaying -> bool;  ctx.now() -> replay_as_of or clock();  ctx.bound(col) -> ColumnElement[bool]
```
`replay_as_of` must be timezone-aware. One context per agent session; the Supervisor builds it
(`Supervisor(..., replay_as_of=...)` for evals) with `sessionmaker(bind=engine)`.
`correlation_id` is the session's default; a per-run cycle id is not threaded through yet — if your
warroom tool needs the current cycle, add it then.

## untrusted()

```python
from atlas_agents.tools import untrusted, detect_instructions
q = untrusted(article.body, article.source)   # UntrustedText(text, source, flags)
q.text     # '<untrusted_source source="coindesk">…</untrusted_source>'
q.flagged  # True when any injection pattern matched; q.flags = matched snippets (≤80 chars each)
```
Every `untrusted_source` tag inside the text (open or close, any case or spacing) is escaped to
`&lt;…`, and the `source` attribute is HTML-escaped, so the text cannot end the quote. Detection runs
on NFKC-normalised text with zero-width/bidi characters removed. Patterns are regexes in
`agents.tools.injection_patterns` (case-insensitive); tune them there, and keep
`test_normal_headlines_are_not_flagged` green.

## The access matrix (`atlas_agents.access`)

- `MATRIX[agent] = AgentAccess(tools={server: names}, web=bool)`. `allowed_tools(agent)`,
  `disallowed_tools(agent)`, `builtin_tools(agent)` and `build_options(agent, ctx, base)` are the
  only way options get tools. `build_options` sets `allowed_tools`, `disallowed_tools` (every
  built-in in `BUILTIN_TOOLS` except the web pair for the web agents), `tools` (CLI base set:
  `["WebSearch","WebFetch"]` or `[]`), `mcp_servers` (only servers with an allowed tool, holding only
  the allowed tools), `strict_mcp_config=True`, `permission_mode="dontAsk"`.
- `options_for(slug, ...)` gives `locked_options()` (no tools at all) for slugs outside the matrix
  (test agents), so unknown agents fail closed. The Supervisor uses it for every session.
- How each §7.10 cell was read is in the module docstring. Choices to know: `atlas_data` "read" and
  "all read" both = every atlas_data tool (all are read-only); `news.web_search` goes with the web
  column (Scout, News only); every agent gets `memory.get_my_scores` (shared preamble) and
  `warroom.post_message/reply/mention`; `request_human_input` is Orchestrator-only; PM also gets
  `memory.append_notebook` (§7.8).
- **The PM's `submit_proposal` must only accept `amend_stop | amend_targets | partial_close | close`**
  — the matrix can't express that; the real tool must check `ctx.agent`.
- Registered names containing "order" are exactly `atlas_data.get_open_orders` and
  `okx_market.get_orderbook` (both read-only, names from §6.2); the test pins that set, so any new
  tool with order/cancel/amend/transfer/withdraw/set_leverage/settings-write in its name fails.

## Not built

No real tool bodies (all stubs). No War Room alert posting for flagged text. No per-run
correlation id in ToolContext. No hallucination guard (separate task).
