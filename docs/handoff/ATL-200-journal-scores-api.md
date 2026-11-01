# ATL-200 — Journal, agent scores and notebook API

Four read-only endpoints for the Journal page, Agents → Scorecards and Portfolio's scorecard section.
All are `GET`, behind `auth:sanctum` (SPA session cookie; 401 without one), and always read the
**active env** (`ActiveEnv::get()` — there is no `?env=` parameter). Nothing writes `audit_log`.

Times are UTC ISO 8601 with microseconds (`2026-10-21T20:59:59.999999Z`). Calendar dates
(`from`, `to`, `date`, `day`) are **Asia/Baghdad** days, `YYYY-MM-DD`. Decimals are strings.
Validation failures are the usual Laravel 422 `{ message, errors: { field: [..] } }`.

Config: `journal_api.*` in `config/defaults.yaml` (page sizes, allowed score windows, scales).

## GET /api/journal

Query (all optional):

| param | meaning |
| --- | --- |
| `inst_id` | exact instId, e.g. `BTC-USDT-SWAP` |
| `agent` | agent slug, e.g. `historian`; matches entries whose **`data.agents`** array contains it (the agents named or voting in the entry, written by ATL-194). The `agent` column is always the writer (`portfolio_manager`) and is not filtered on. |
| `tag` or `tag[]` | one tag or several; an entry matches if it has **any** of them (≤ 20) |
| `outcome` | `win` \| `loss` \| `scratch`; matches `data.outcome` **or** a bare tag of that value |
| `from`, `to` | Baghdad days, inclusive; `to` ≥ `from` |
| `limit` | 1–200, default 50 |
| `cursor` | `meta.next_cursor` from the previous page; a tampered cursor is a 422 |

Order: `created_at` desc, then `id` desc. Filters are kept in the cursor's links, but pass them again
with `cursor` (the SPA builds the URL itself).

```json
{
  "data": [
    {
      "id": "9f1c2a40-6a0e-4c1e-9b8e-2d5f0c7a1b23",
      "env": "demo",
      "title": "BTC long stopped before TP1",
      "inst_id": "BTC-USDT-SWAP",
      "agent": "portfolio_manager",
      "proposal_id": "0b7e3c1d-2f4a-4b5c-8d6e-7f8091a2b3c4",
      "expected_md": "Pullback to 67,200 support, TP1 68,900 (1.8R), p=0.55.",
      "actual_md": "Stopped at 66,450 after 3h 10m; −1.02R, −512.40 USD.",
      "lessons_md": "Wait for a 1H close back above the level before entering a pullback.",
      "tags": ["pullback_to_support", "crypto", "trend", "none", "loss"],
      "data": { "outcome": "loss", "realised_r": "-1.02", "agents": ["strategist", "technical_analyst", "historian", "devils_advocate"] },
      "created_at": "2026-10-21T20:59:59.999999Z"
    }
  ],
  "meta": {
    "env": "demo",
    "per_page": 50,
    "next_cursor": "eyJjcmVhdGVkX2F0Ijoi...",
    "prev_cursor": null
  }
}
```

`data` is passed through exactly as the writer stored it (ATL-194 owns its keys; see that task's
handoff). An empty object is `{}`, never `[]`. `tags` is always an array (possibly empty).
Markdown fields can be `null`.

## GET /api/journal/{id}

`{id}` must be a UUID (anything else is a 404). An entry of the other env is a **404**.

```json
{ "data": { "id": "9f1c2a40-…", "env": "demo", "title": "…", "inst_id": "BTC-USDT-SWAP",
  "agent": "portfolio_manager", "proposal_id": null, "expected_md": "…", "actual_md": "…",
  "lessons_md": "…", "tags": ["breakout"], "data": {}, "created_at": "2026-10-20T10:00:00.250000Z" } }
```

## GET /api/journal/weekly-reviews

The Historian's Sunday weekly reviews: the active env's `research_reports` rows of kind
`weekly_review`, newest first (`created_at` desc, then `id` desc). Query: `limit` (1–200, default 50)
and `cursor` (`meta.next_cursor`; a tampered cursor is a 422). Another env's reviews and other report
kinds are never returned.

```json
{
  "data": [
    {
      "id": "3c9a7e10-1b2d-4e5f-8a9b-0c1d2e3f4a5b",
      "env": "demo",
      "agent": "historian",
      "week_start": "2026-10-19",
      "week_end": "2026-10-25",
      "content_md": "## Week of 19 Oct\nBreakouts made +3.1R; range fades lost −1.4R…",
      "data": { "week_start": "2026-10-19", "setups": [{ "name": "breakout", "r": "1.4" }] },
      "created_at": "2026-10-25T15:00:00.000000Z"
    }
  ],
  "meta": { "env": "demo", "per_page": 50, "next_cursor": null, "prev_cursor": null }
}
```

- `week_start` / `week_end` are Baghdad dates (Monday / Sunday of the reviewed week). `week_start` is
  `data.week_start` when the writer stored a valid `YYYY-MM-DD` there; otherwise it is the Monday of the
  Baghdad week `created_at` falls in (the review runs Sunday 18:00, the week's last day). **Writer of the
  weekly review: please store `data.week_start` as `YYYY-MM-DD`.**
- `data` is passed through unchanged (`{}` when empty); `content_md` is agent-written markdown.
- The Journal page draws one weekly row per review at the end of its week: place it after the entries
  whose Baghdad date is ≤ `week_end`.

## GET /api/agents/scores?days=30

`days` ∈ `7`, `30`, `90` (default 30; anything else is 422). The window is the last `days` Baghdad
days **ending today** (inclusive), e.g. days=7 on 2026-10-28 → 2026-10-22 … 2026-10-28.

Every roster agent is listed in roster order (`agents.roster`), even with no rows; an agent slug seen
only in `agent_scores` is appended after them.

```json
{
  "data": [
    {
      "agent": "market_scout",
      "name": "Market Scout",
      "summary": {
        "n": 20,
        "days_scored": 4,
        "hit_rate": "0.6000",
        "brier_score": "0.250000",
        "reliability_weight": "0.9",
        "reliability_weight_day": "2026-10-27"
      },
      "days": [
        { "day": "2026-10-22", "n": 4, "hit_rate": "0.2500", "brier_score": "0.300000",
          "data": { "selection_edge": "0.4" } },
        { "day": "2026-10-25", "n": 6, "hit_rate": "0.5000", "brier_score": "0.200000",
          "data": { "reliability_weight": "1.2" } },
        { "day": "2026-10-27", "n": 10, "hit_rate": "0.8000", "brier_score": null,
          "data": { "reliability_weight": "0.9" } },
        { "day": "2026-10-28", "n": 0, "hit_rate": null, "brier_score": null, "data": {} }
      ]
    },
    {
      "agent": "historian",
      "name": "Historian",
      "summary": { "n": 0, "days_scored": 0, "hit_rate": null, "brier_score": null,
        "reliability_weight": null, "reliability_weight_day": null },
      "days": []
    }
  ],
  "meta": { "env": "demo", "days": 7, "from": "2026-10-22", "to": "2026-10-28", "timezone": "Asia/Baghdad" }
}
```

Summary rules (code, `AgentScoresController::summary`):

- `n` = sum of the window's `n`; `days_scored` = number of rows in the window.
- `hit_rate` = Σ(hit_rate × n) / Σ n over days that have a hit rate and n > 0, 4 dp; `null` if none.
- `brier_score` = plain mean of the days that have one, 6 dp; `null` if none.
- `reliability_weight` = the newest day's numeric `data.reliability_weight` (as a string), with the
  day it came from; `null` if no day in the window has one. It is what votes read (`atlas_agents.votes`).
- `days[].data` is the scorer's own object, unchanged — each agent's scorer (ATL-195 etc.) owns its
  keys, so the Scorecards UI should read them per agent and ignore unknown ones.

## GET /api/agents/notebook?date=2026-10-20

`date` = Baghdad day, default today in Baghdad. Notes of the active env, oldest first, at most
`journal_api.notebook_max_notes` (500).

```json
{
  "data": [
    { "id": "5b0e…", "agent": "historian", "note_md": "Regime table posted; XAU trending.",
      "data": {}, "created_at": "2026-10-20T05:00:00.000000Z" }
  ],
  "meta": { "env": "demo", "date": "2026-10-20", "timezone": "Asia/Baghdad" }
}
```

## Notes for the page builders

- Text in `expected_md`, `actual_md`, `lessons_md` and `note_md` is agent-written markdown that may
  quote news; render it as markdown without raw HTML.
- No totals or counts are returned for the journal (cursor pagination), so the page should not show
  "N entries".
- The outcome convention (`data.outcome` plus a bare `win|loss|scratch` tag) was agreed with ATL-194.
