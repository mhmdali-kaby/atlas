# ATL-78 — Agent runtime tables

Migration: `backend/database/migrations/2026_10_29_400000_create_agent_tables.php`.
Python models: `atlas_shared.db` (`services/shared/src/atlas_shared/db/models.py`), helpers in
`atlas_shared/db/agents.py`. Pest: `tests/Feature/Schema/AgentSchemaTest.php`. pytest:
`shared/tests/test_agent_models.py`. The schema-drift test (`test_schema_reflection.py`) covers all eight.

## The two-minute version

- **`agent` is a plain `varchar(64)` slug** from `config/defaults.yaml` `warroom.agents`
  (`orchestrator`, `market_scout`, `news_intelligence`, `historian`, `technical_analyst`,
  `derivatives_sentiment`, `strategist`, `devils_advocate`, `portfolio_manager`). There is **no DB
  CHECK** on it — the runtime validates it, so adding an agent never needs a migration.
- Every table: `id uuid` pk (models default `uuid4`), `created_at`/`updated_at timestamptz(6)` nullable
  (set them yourself from Python; Laravel sets them from Eloquent). Enum columns are Laravel enums:
  `varchar(255)` + CHECK.
- Every jsonb column is **NOT NULL DEFAULT '{}'**. Omit it on insert and you get `{}`; never write
  `None` to it. `journal_entries.tags` is `text[] NOT NULL DEFAULT '{}'`.
- **Daily usage: always write through `add_usage()`**, never `session.add(AgentUsageDaily(...))`.
  It is one `INSERT … ON CONFLICT (agent, env, day) DO UPDATE SET x = agent_usage_daily.x + excluded.x`,
  so two concurrent runs never lose tokens. It flushes, does not commit.
- `day` columns (usage, notebook, scores) are the **Asia/Baghdad** date: use `usage_day(ts)`.
- Money/ratios are `Decimal` (`Numeric(asdecimal=True)`), never float.

```python
from atlas_shared.db import add_usage, usage_day
row = add_usage(session, agent="historian", env="demo", day=usage_day(ended_at),
                tokens_in=1200, tokens_out=300, turns=4, runs=1)   # returns the summed row
session.commit()
```

## Tables

### `agent_runs` — model `AgentRun` (has `env`)
| column | type | notes |
|---|---|---|
| env | enum `demo`,`live` | |
| agent | varchar(64) | slug |
| trigger | enum `mention`,`schedule`,`eval`,`manual` | |
| correlation_id | varchar(191) null | e.g. `cycle-2026-10-29`; indexed |
| started_at | timestamptz NOT NULL | |
| ended_at | timestamptz null | |
| status | enum `running`,`ok`,`error`,`degraded`,`rate_limited`,`over_budget` | default `running` |
| model | varchar(100) null | |
| prompt_version | int null | `prompt_versions.version` (no FK) |
| tokens_in, tokens_out | bigint default 0 | |
| turns | int default 0 | |
| cost_usd_notional | numeric(18,6) default 0 | SDK figure, display only (#242) |
| error | text null | |

Indexes: (agent, started_at), (env, started_at), (correlation_id).

### `agent_usage_daily` — `AgentUsageDaily`
agent varchar(64), env enum, day date (Baghdad), tokens_in bigint, tokens_out bigint, turns int,
runs int (all default 0). **Unique (agent, env, day).** Write with `add_usage()`.

### `agent_states` — `AgentState`
agent varchar(64), env enum, state enum `idle`,`thinking`,`waiting`,`degraded`,`rate_limited`,`paused`,`disabled`
(default `idle`), current_task text null, state_since timestamptz NOT NULL default now(),
last_error text null. **Unique (agent, env).** Upsert with
`insert(AgentState.__table__).on_conflict_do_update(index_elements=["agent","env"], set_=…)`;
set `state_since` only when `state` actually changes.

### `prompt_versions` — `PromptVersion` (no env)
agent varchar(64), version int (CHECK ≥ 1), sha256 char(64) (hex of body), body text.
**Unique (agent, version) and (agent, sha256)** — the same body can't be stored twice under a new
number; look up by (agent, sha256) first and reuse that version.

### `research_reports` — `ResearchReport` (has env)
agent varchar(64), agent_run_id uuid null FK → agent_runs ON DELETE SET NULL, inst_id varchar(64) null,
kind varchar(64) (free text, e.g. `regime_table`, `history_verdict`), content_md text,
**data jsonb NOT NULL '{}'**, confidence numeric(6,4) null CHECK 0..1, valid_until timestamptz null.
Indexes: (env, inst_id, created_at), (agent, created_at).

### `agent_notebook` — `AgentNotebookEntry` (has env)
day date (Baghdad), agent varchar(64), note_md text, **data jsonb NOT NULL '{}'**. Index (env, day, created_at).
One row per note (append-only), not one row per day.

### `journal_entries` — `JournalEntry` (has env)
agent varchar(64), inst_id varchar(64) null, proposal_id uuid null (**no FK yet** — proposals arrive in
Phase 2), title varchar(255), expected_md / actual_md / lessons_md text null,
**data jsonb NOT NULL '{}'**, **tags text[] NOT NULL '{}'** (GIN index `journal_entries_tags_gin`;
query with `JournalEntry.tags.contains(["loss"])`). Index (env, inst_id, created_at).

### `agent_scores` — `AgentScore` (has env)
agent varchar(64), day date (Baghdad), n int default 0, hit_rate numeric(6,4) null CHECK 0..1,
brier_score numeric(8,6) null CHECK 0..1, **calibration jsonb NOT NULL '{}'**,
pnl_attributed_usd numeric(38,18) null, **data jsonb NOT NULL '{}'** (agent-specific metrics, e.g.
Scout's selection edge). **Unique (agent, env, day).**

## Not built here

- No Eloquent models or API — the Agents page task adds them when it reads these tables.
- No retention/pruning of `agent_runs`.
- No FK from `journal_entries.proposal_id` or `agent_runs.prompt_version`.
