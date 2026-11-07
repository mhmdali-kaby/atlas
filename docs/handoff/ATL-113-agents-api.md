# ATL-113 — Agents API and agent settings

For ATL-117 (Agents page), ATL-118 (Settings → Agents) and ATL-114 (supervisor applies settings).

Code: `routes/api/agents.php`, `App\Http\Controllers\Agents\AgentsController`,
`App\Support\Agents\AgentRoster`, `App\Support\Doctor\Doctor::claudeChecks()`, agent fields in
`App\Support\Settings\SettingsSchema` + `UpdateSettingsRequest`. Tests:
`tests/Feature/Agents/AgentsApiTest.php`, `tests/Feature/Settings/AgentSettingsApiTest.php`.

## The two-minute version

- Agent settings are **ordinary settings fields** with dotted names, stored **flat** in `settings.data`
  (`data["agents.historian.enabled"]`, not `data.agents.historian.enabled`). Read them with
  `GET /api/settings`, write them with the existing `PATCH /api/settings` (version check, 409, history,
  audit, `system.settings_changed` — all unchanged, see `ATL-3-settings.md`).
- `changed_fields` in `system.settings_changed` lists the dotted names, e.g.
  `["agents.market_scout.enabled"]`. **ATL-114**: reload `settings.data` for the env and read
  `agents.<slug>.*` and `agents.web.*` from it; config/defaults.yaml is only the default for a row saved
  before a field existed (Laravel fills it on read, Python should do the same with `agents.defaults` +
  `agents.roster` merged).
- `GET /api/agents` never writes agent state; it reads `settings`, `agent_states`, `agent_runs`,
  `prompt_versions`. An agent that is not enabled is reported `state: "disabled"` whatever its
  `agent_states` row says (and `current_task`/`state_since` are null then).
- "Today" = the Asia/Baghdad day (`timezone.display`): runs whose `started_at` is in
  [Baghdad 00:00, next Baghdad 00:00). `today.tokens = tokens_in + tokens_out` — the same sum the runtime
  budgets against `daily_tokens`.
- `GET /api/agents/claude-status` runs the `claude` CLI (`--version`, `auth status --json`, and only if
  that is unparseable, one `-p ok --max-turns 1` probe) every call. Don't poll it; fetch on page open and
  on a "Check again" button.

## Settings keys

For each of the nine roster slugs (`orchestrator`, `market_scout`, `news_intelligence`, `historian`,
`technical_analyst`, `derivatives_sentiment`, `strategist`, `devils_advocate`, `portfolio_manager`):

| key | JSON type | allowed | default |
| --- | --- | --- | --- |
| `agents.<slug>.enabled` | boolean (strict: `true`/`false`, not `"false"` or `0`) | | `agents.defaults.enabled` (false), roster override (historian: true) |
| `agents.<slug>.model` | string | `strong` \| `standard` \| `routine` | `standard`; `strong` for market_scout, news_intelligence, historian, strategist, devils_advocate |
| `agents.<slug>.daily_tokens` | integer (strict) | 1 – 1,000,000,000 | 3,000,000 |
| `agents.<slug>.daily_turns` | integer (strict) | 1 – 100,000 | 300 |

Plus:

| key | JSON type | allowed | default |
| --- | --- | --- | --- |
| `agents.web.allow_domains` | array of strings (a JSON list) | ≤ 200 entries, each a bare hostname | `[]` |
| `agents.web.deny_domains` | array of strings | ≤ 200 entries, each a bare hostname | `[]` |

Bounds live in `config/defaults.yaml` → `agents.settings_bounds`. Tier → CLI model alias is
`agents.models` (`strong: opus`, `standard: sonnet`, `routine: haiku`).

Hostnames are stored normalised: trimmed, one trailing dot dropped, lower-cased, IDNA/punycode
(UTS #46 non-transitional), duplicates removed, order kept. `"CoinDesk.com"` → `"coindesk.com"`,
`"bücher.example"` → `"xn--bcher-kva.example"`. Refused (422): anything with a scheme, `/`, `:`
(port), `@`, `?`, `#`, `*`, `%`, `\`, brackets, whitespace, an empty string, a label over 63 chars or a
name over 253. Single-label entries (`"ru"`) are allowed: per ATL-100's matching rule an entry matches
the host and its subdomains. An empty array clears a list. The whole list is replaced on every PATCH.

### PATCH example

```json
PATCH /api/settings
{ "env": "demo", "version": 4, "changes": {
    "agents.market_scout.enabled": false,
    "agents.news_intelligence.model": "routine",
    "agents.news_intelligence.daily_tokens": 500000,
    "agents.web.deny_domains": ["Bücher.example", "x.com"]
} }
```

200 → the usual settings document; `data.settings` now contains every field, including:

```json
"agents.market_scout.enabled": false,
"agents.news_intelligence.model": "routine",
"agents.news_intelligence.daily_tokens": 500000,
"agents.web.deny_domains": ["xn--bcher-kva.example", "x.com"]
```

422 bodies (error keys are the dotted names with the `changes.` prefix; list entries get their index):

```json
{ "message": "The selected agents.historian.model is invalid. (and 2 more errors)",
  "errors": {
    "changes.agents.historian.model": ["The selected agents.historian.model is invalid."],
    "changes.agents.historian.daily_tokens": ["The agents.historian.daily_tokens field must be between 1 and 1000000000."],
    "changes.agents.web.deny_domains.0": ["Each entry must be a bare hostname such as \"coindesk.com\", with no scheme, port or path."]
  } }
```

An unknown key (e.g. `agents.trader.enabled`) → `errors.changes`. Nothing is saved on any 422.

`settings_history.diff` and `audit_log` carry the new types as they are: `{"agents.market_scout.enabled":
{"from": true, "to": false}}`, and lists as arrays. **ATL-118 / any Activity list**: a diff value can
now be a boolean or an array, not only a string or number.

## GET /api/agents?env=demo

Sanctum session; 401 without. `env` optional (`demo` default), else 422 on `env`. Always the nine
roster agents, in roster order.

```json
{
  "data": [
    {
      "slug": "historian",
      "name": "Historian",
      "handle": "Historian",
      "enabled": true,
      "model": "strong",
      "model_name": "opus",
      "state": "thinking",
      "state_since": "2026-10-30T11:59:00.000000Z",
      "current_task": "Regime table for the shortlist",
      "today": {
        "tokens": 1265,
        "tokens_in": 1057,
        "tokens_out": 208,
        "turns": 6,
        "runs": 3,
        "cost_usd_notional": "0.112345"
      },
      "budget": { "daily_tokens": 3000000, "daily_turns": 300 },
      "prompt": {
        "sequence": 2,
        "sha": "b1c2d3e4f5a6",
        "sha256": "b1c2d3e4f5a60000000000000000000000000000000000000000000000000000",
        "created_at": "2026-10-30T07:00:00.000000Z"
      },
      "last_run": {
        "id": "6f1c0e8a-5b0d-4d8e-9a51-2b7f1c2d3e4f",
        "status": "rate_limited",
        "trigger": "schedule",
        "started_at": "2026-10-30T11:30:00.000000Z",
        "ended_at": "2026-10-30T11:31:12.000000Z",
        "prompt_version": 2
      }
    },
    {
      "slug": "market_scout",
      "name": "Market Scout",
      "handle": "Scout",
      "enabled": false,
      "model": "strong",
      "model_name": "opus",
      "state": "disabled",
      "state_since": null,
      "current_task": null,
      "today": { "tokens": 0, "tokens_in": 0, "tokens_out": 0, "turns": 0, "runs": 0, "cost_usd_notional": "0.000000" },
      "budget": { "daily_tokens": 3000000, "daily_turns": 300 },
      "prompt": null,
      "last_run": null
    }
  ],
  "meta": {
    "env": "demo",
    "day": "2026-10-30",
    "timezone": "Asia/Baghdad",
    "day_start": "2026-10-29T21:00:00.000000Z",
    "day_end": "2026-10-30T21:00:00.000000Z",
    "settings_version": 4
  }
}
```

Field notes:

- `state`: `agent_states.state` for (agent, env) — `idle | thinking | waiting | degraded | rate_limited |
  paused | disabled` — or `idle` when the agent is enabled and has no row yet; always `disabled` when
  `enabled` is false.
- `cost_usd_notional` is a decimal **string** (6 places), the SDK's figure. Display it as notional only
  (decision #242: billing is the subscription; budgets are tokens and turns).
- `budget` comes from the settings, i.e. what the owner set — the same numbers ATL-114 must enforce.
- `prompt`: the version the agent's newest run used; if it has never run, the newest stored version;
  `null` when it has no prompt_versions row. Show `sha` (12 hex, ATL-90's UI version) and
  `sequence` as "v2".
- `last_run`: newest `agent_runs` row in this env on any day, or null. `status` is `running | ok | error
  | degraded | rate_limited | over_budget`. The run's `error` text is deliberately not returned.
- `today.runs` counts runs of every status, including errors.

## GET /api/agents/claude-status

Sanctum session; 401 without. Never contains an API key value or an email address: the CLI's
`auth status --json` output (which includes the account email) is only read for `loggedIn`; the key
check reports variable names only; every `detail` is additionally scrubbed of email-shaped text.

```json
{
  "data": {
    "ok": false,
    "installed": true,
    "logged_in": true,
    "api_key_env_clear": false,
    "login_command": "claude login",
    "checks": [
      { "name": "claude_cli", "ok": true, "status": "pass", "detail": "Installed (2.4.1 (Claude Code))" },
      { "name": "claude_cli_login", "ok": true, "status": "pass", "detail": "Logged in" },
      { "name": "claude_api_key", "ok": false, "status": "fail", "detail": "ANTHROPIC_API_KEY is set. Unset it so agent usage cannot silently fall back to pay-as-you-go." }
    ],
    "checked_at": "2026-10-30T12:00:00.000000Z"
  }
}
```

- `ok` = all three pass. When `installed` is false, `logged_in` is false too and its detail is
  "Not checked: the claude CLI was not found" (the login check does not run without the CLI).
- Logged out: `claude_cli_login` detail is "Not logged in. Run `claude login` in a terminal on this Mac,
  then check again." — show `login_command` as a copyable code line.
- Details are the Doctor's English lang strings (`lang/en/doctor.php`); translate on the client by
  `name` + `status` if Arabic copy is needed.
- The env check reads the **Laravel** process's environment (backend/.env included) — the same rule as
  `atlas:doctor`. It is not a probe of the agents process's environment; the runtime strips the keys there
  itself.

## Not built here

- No Eloquent models for the agent tables (query builder in `AgentRoster`).
- No PATCH under `/api/agents`; enable/disable is a settings change by design (versioned + audited).
- No live push of agent states: the page polls `GET /api/agents` or listens to `system.agent_state`
  (ATL-83) on `private-system` if the bridge rebroadcasts it.
- No caching or throttle on claude-status.
