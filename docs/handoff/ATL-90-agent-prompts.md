# ATL-90 — Versioned agent prompts

For the Historian agent task (and patch E / Phase 2 agents).

## Files

`services/agents/prompts/`:

- `_preamble.md` — the brief's SHARED PREAMBLE verbatim (without its "SHARED PREAMBLE (prepend to every
  agent)" label line), with `{{ENV}}` and `{{DATE}}`.
- one `<slug>.md` per agent: `orchestrator`, `market_scout`, `news_intelligence`, `historian`,
  `technical_analyst`, `derivatives_sentiment`, `strategist`, `devils_advocate`, `portfolio_manager` —
  each the brief's `ROLE:` block verbatim.

`test_prompt_files_match_the_brief_verbatim` re-extracts the fenced blocks from the repo's `CLAUDE.md`
and compares byte for byte. **If you deliberately change a role prompt** (patch E, Phase 2), that test
will fail: change the test to compare against the brief for the agents you have not touched, or record the
divergence — do not just delete it.

## API (`atlas_agents.prompts`)

```python
render(agent_slug: str, ctx: PromptContext, *, source: PromptSource | None = None,
       prompts_dir: Path | None = None) -> str
load_context(tool_ctx: ToolContext) -> PromptContext       # settings + last 7 days of scores from Postgres
renderer(tool_ctx, source) -> Callable[[], str]            # the hook AgentSession calls at connect
load_source(agent, prompts_dir=None) -> PromptSource       # .version (12 hex), .sha256, .body
sync_versions(engine, agents=None, *, prompts_dir=None) -> dict[slug, (PromptSource, row_version:int)]
PromptContext(env, today: date, settings: Mapping[str, int|Decimal] | None, settings_version, scores)
ScoreLine(day, n, hit_rate, brier_score, pnl_attributed_usd)
PromptError(ValueError)
```

Rendered order: preamble, role, `## Current settings`, `## Your last 7 days scorecard`, separated by a
blank line.

- **Current settings**: `Settings version N. Read-only; ...` then `- key: number` lines, sorted, from
  `tools.account.load_settings` — the exact `SETTINGS_SNAPSHOT_KEYS` allow-list of
  `get_settings_snapshot` (live: `settings`; replay: `settings_history` at the cutoff). No row →
  `no settings saved`.
- **Scorecard**: a markdown table `| day | n | hit_rate | brier_score | pnl_attributed_usd |`, newest first,
  from `agent_scores` for this agent and env with `today-7 <= day < today` (Baghdad day; today's score is
  not complete yet) and `created_at` bounded in replay. None → `no scores yet`.

## Template variables

`{{NAME}}` (spaces inside the braces allowed). Available: `ENV`, `DATE` (Baghdad ISO date),
`MAX_PICKS` (= `max_instruments_per_day`), `MIN_R` (= `min_expected_r`), and every allow-listed settings
key upper-cased (e.g. `{{MAX_LEVERAGE}}`). An unknown variable, or one whose setting is not saved (e.g.
`MIN_R` with no settings row), raises `PromptError`; any `{{` left in the output also raises. Currently used:
preamble `ENV`, `DATE`; `market_scout` `MAX_PICKS`; `technical_analyst` `MIN_R`. Historian uses none.

## Version rule

`version = sha256(preamble bytes + role bytes)[:12]`, before any template is filled or block generated —
settings and scores never change it, one changed byte does.

ATL-78's `prompt_versions` table has `version integer (>= 1)` and `sha256 char(64)`, and
`agent_runs.prompt_version` is an integer, so the schema was kept and mapped like this:

- `prompt_versions.sha256` = full hex hash (its first 12 hex = the version above),
  `prompt_versions.body` = raw preamble + role, `prompt_versions.version` = per-agent sequence 1, 2, 3…
- `agent_runs.prompt_version` = that sequence number, so a run resolves to
  `prompt_versions(agent, version)` → `sha256[:12]` and the exact body.

Show `sha256[:12]` in the UI as the version.

## Supervisor wiring

`Supervisor.__init__` calls `prompts.sync_versions` for every enabled agent that has a prompt file
(inserted once per (agent, sha256); a second start inserts nothing), then gives each `AgentSession`
`prompt_version=<row number>` and `system_prompt=prompts.renderer(ctx, source)`. `AgentSession` gained an
optional `system_prompt: Callable[[], str]`; it is called in `_ensure_client` right before each connect,
so a new session (or a reconnect after an error) gets current settings, scores and date. A render failure
makes that run `error` / agent `degraded`, never a crash. `Supervisor(prompts_dir=...)` overrides the folder
(tests). Agents without a prompt file (test slugs) get no system prompt and `prompt_version=None`.

## Two-minute version

The files are read once at supervisor start, and that snapshot is what gets rendered and versioned, so
editing a prompt needs a supervisor restart to take effect. The DATE in the prompt is the date of the
session's last connect: a session kept open past Baghdad midnight still says yesterday until it reconnects
— the scheduling task should close/reconnect sessions at the day boundary if that matters.
