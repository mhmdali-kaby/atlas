# ATL-83 — Agent runtime core

Package `services/agents/src/atlas_agents`. Tests: `services/agents/tests` (fake SDK only; DB tests
need `cd backend && php artisan test` run first in the checkout).

## The two-minute version

- **Build sessions with `AgentSession(spec, env=..., engine=..., options=ClaudeAgentOptions(...))`.**
  Pass your `allowed_tools`, `mcp_servers`, `system_prompt` in `options`; the session fills in
  `model` (spec), `max_turns` (spec), and defaults `tools=[]`, `setting_sources=[]`, `skills=[]` when
  you leave them `None` — so an agent has **no built-in tools** (no Bash/Read/WebSearch) and never
  loads `~/.claude` settings or CLAUDE.md. ATL-84: set `tools=[...]` explicitly for web tools.
- **No API key ever reaches the CLI.** The SDK merges `os.environ` into the child env and
  `options.env` cannot remove keys, so the runtime **pops `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` from this process's `os.environ`** before every connect. Passing either in
  `options.env` raises `ForbiddenCredentialError` at construction. Don't rely on those vars anywhere
  in the agents process.
- **`run()` never raises for a CLI/SDK failure** — it returns a `RunResult` with a status. Only DB
  failures escape it; `Supervisor.run()` catches those too. After any exception the client is
  disconnected and the next run reconnects.
- One `ClaudeSDKClient` per agent, connected lazily on the first run, kept between runs. All calls
  must happen in the same asyncio task (SDK restriction) — the supervisor loop is that task.

## API

```python
from atlas_agents.registry import REGISTRY, get_spec, enabled_agents, AgentSpec
from atlas_agents.session import AgentSession, RunResult
from atlas_agents.supervisor import Supervisor, AgentUnavailableError

sup = Supervisor(env="demo", engine=make_engine())          # sessions for enabled agents only
res = await sup.run("historian", "Post the regime table", trigger="schedule",
                    task="regime table", correlation_id="cycle-2026-10-29", routine=False)
res.status      # "ok" | "error" | "rate_limited" | "over_budget"
res.text        # concatenated assistant TextBlocks
res.run_id, res.tokens_in, res.tokens_out, res.turns, res.cost_usd_notional (Decimal),
res.error, res.resets_at (datetime|None), res.messages (raw SDK messages, for warroom.thinking)
await sup.close()
```

`AgentSession(spec, *, env, engine, options=None, client_factory=default_client_factory,
publisher=events.bus_publisher, prompt_version=None, clock=None)`;
`await session.run(prompt, *, trigger="manual", task=None, correlation_id=None, routine=False)`;
`await session.close()`. `routine=True` runs on `spec.routine_model` via `client.set_model()` and
switches back on the next normal run. `trigger` must be one of `mention|schedule|eval|manual`.

`AgentSpec(slug, name, model, routine_model, enabled, daily_tokens, daily_turns, max_turns_per_run)`
built from `config/defaults.yaml` `agents:` (tiers `strong=opus`, `standard=sonnet`,
`routine=haiku` — CLI aliases, not pinned ids). Only `historian` is enabled. Strong: scout, news,
historian, strategist, devil.

`check_claude_cli()` → `{"installed": bool, "version": str|None, "logged_in": bool, "hint": str|None}`.
Runs `claude --version` and `claude auth status --json` (reads `loggedIn`) with the Anthropic vars
removed; returns no email/org. CLI: `uv run --package atlas-agents python -m atlas_agents.cli_check`
prints that JSON, exit 0 only when logged in (for `atlas:doctor`).

## What a run writes

1. **Refusals (no client call):** today's (Baghdad day) `tokens_in+tokens_out ≥ daily_tokens` or
   `turns ≥ daily_turns` → `agent_runs.status=over_budget`, state **`degraded`** (agent_states has
   no `over_budget` value), `last_error="over_budget: daily tokens budget reached (…)"`.
   A rate limit whose reset time is known and still in the future → `status=rate_limited`.
2. Otherwise `agent_runs` row `running` + state `thinking` (current_task = task or trigger), commit.
3. After the response: row gets `ended_at, status, tokens_in, tokens_out, turns,
   cost_usd_notional, error, model, prompt_version`; `add_usage(..., runs=1)`; state
   `ok→idle`, `error→degraded` (last_error), `rate_limited→rate_limited`.

Token counting: `tokens_in = input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
from the `ResultMessage.usage`; `turns = num_turns`. Cost is the **delta** of the session's running
`total_cost_usd` (display only). With no ResultMessage, usage is summed from assistant messages.

Rate limit is detected from: `RateLimitEvent` with `status="rejected"` (gives `resets_at`),
`AssistantMessage.error == "rate_limit"`, an error `ResultMessage` with `api_error_status == 429`,
`ResultError` with 429, or error text matching `usage limit|rate limit|429|limit reached`.
The reset time lives in memory on the session and in `last_error` text (no column for it).

## Events (stream `atlas.system`, brief §3.2 envelope, `source = "agent:<slug>"`)

`system.agent_state` — on every actual state change (incl. idle→thinking→idle):
```json
{"agent": "historian", "state": "rate_limited", "previous_state": "thinking",
 "reason": "rate_limited", "run_id": "6f1c…", "current_task": null,
 "last_error": "Claude usage limit reached. (resets at 2026-10-29T13:00:00+00:00)",
 "resets_at": "2026-10-29T13:00:00+00:00"}
```
`reason`: `run_started | ok | error | rate_limited | over_budget`. `previous_state` is null for an
agent with no row yet.

`system.agent_budget` — once, when a run crosses `agents.budget_warning_pct` (80) of either budget:
```json
{"agent": "historian", "day": "2026-10-29", "threshold_pct": "80", "crossed": ["tokens"],
 "tokens_used": 8500, "tokens_budget": 10000, "turns_used": 1, "turns_budget": 100}
```

Publishing failures (Redis down) are logged and never fail a run.

## FakeClaudeClient (`atlas_agents.testing`)

```python
from atlas_agents import testing as fake
client = fake.FakeClaudeClient([
    [fake.tool_use("mcp__history__compute_stats", {"inst_id": "BTC-USDT-SWAP"}),
     fake.tool_result('{"as_of": "…"}'),
     fake.assistant_text("Regime: trend up."),
     fake.result(tokens_in=1000, cache_read=150, tokens_out=300, turns=2, cost_usd=0.0125)],
    [fake.rate_limit_rejected(resets_at=1793000000), fake.rate_limit_result()],
    [RuntimeError("CLI crashed")],            # an exception step is raised when reached
])
session = AgentSession(spec, env="demo", engine=engine,
                       client_factory=fake.factory(client), publisher=recorder)
```
Script = list of runs; each `query()` pops one run; a run is a list of real SDK message objects or
exceptions; replay stops after a `ResultMessage`. It records `queries`, `models` (set_model calls),
`connects`, `disconnects`, and `options` (what the session built). A query with no run left raises
`ScriptExhaustedError`.

## Not built here

No `python -m atlas_agents` process, mention consumer or schedule (later tasks); no tool access
matrix (ATL-84); no prompt files / `prompt_versions` writes (pass `prompt_version` yourself).
