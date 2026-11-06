# ATL-251 — Prompt versions API and the prompt pin

Brief §14.7, decision #265 (3). For the Settings → Agents prompt UI (view / diff / rollback) and the
supervisor task that builds a pinned prompt from a stored body.

All routes need the Sanctum SPA session (`auth:sanctum`). `{slug}` must be a roster agent
(`SettingsSchema::agentSlugs()`); anything else is **404**. Only stored raw bodies
(`_preamble.md` + `<slug>.md`, before templates are filled) are returned — never a rendered prompt.

## GET /api/agents/{slug}/prompts[?env=demo|live]

`env` defaults to the **active env** (`ActiveEnv::get()`); it scopes `runs_count` and `is_pinned`.
Newest version first.

```json
{
  "data": [
    {"version": 3, "hash12": "9f1c0e7a2b44", "created_at": "2026-11-03T08:00:00.000000Z", "runs_count": 0, "is_current_file": false, "is_pinned": false},
    {"version": 2, "hash12": "51aa03d9c2e0", "created_at": "2026-11-02T08:00:00.000000Z", "runs_count": 1, "is_current_file": true,  "is_pinned": false},
    {"version": 1, "hash12": "c03b9e11f7d2", "created_at": "2026-11-01T08:00:00.000000Z", "runs_count": 2, "is_current_file": false, "is_pinned": true}
  ],
  "meta": {"agent": "historian", "env": "demo", "pinned_version": 1}
}
```

- `is_current_file`: the row whose sha256 equals sha256(`_preamble.md` bytes + `<slug>.md` bytes) in
  `config('services.agent_prompts.dir')` (env `ATLAS_AGENT_PROMPTS_DIR`, default
  `../services/agents/prompts`) — the same rule as `prompts.load_source()`. At most one is true; none
  when the file is missing or the supervisor has not synced it yet. It is **not** always the newest
  row (a file reverted in git maps back to an older version).
- `runs_count`: `agent_runs` rows of this agent in `env` with that `prompt_version`.

## GET /api/agents/{slug}/prompts/{version}

```json
{"data": {"version": 1, "hash12": "c03b9e11f7d2", "body": "You are one member of \"Atlas\"...\nROLE: Historian. ..."}}
```

A version that belongs to another agent (or does not exist) → 404. The diff (jsdiff) is computed in
the SPA from two of these bodies.

## PUT /api/agents/{slug}/prompt-pin

Request: `{"version": 1}` or `{"version": null}` (clear), optional `"env": "demo"|"live"` (default:
active env). `version` must be present and a JSON integer ≥ 1 or null.

Response 200:

```json
{"data": {"agent": "historian", "env": "demo", "pinned_version": 1, "settings_version": 7}}
```

- 422 `errors.version` when the version is not a stored version of that agent (or is a string / missing).
- 409 `{message, current_version}` only if two concurrent settings saves beat it twice (it retries once).
- Writes through `SettingsService::update`, so a real change bumps the settings version and writes
  `settings_history`, `audit_log` (`settings.updated`) and `system.settings_changed` on `atlas.system`
  with `changed_fields: ["agents.historian.prompt_version"]`. Pinning the already-pinned value is a
  no-op (no new version, `settings_version` unchanged).

## The settings key

`agents.<slug>.prompt_version` — `int|null`, default `null` (= use the current file). It is part of
`SettingsSchema::AGENT_SETTINGS` (kind `prompt_version`), so it appears in `GET /api/settings`
`data.settings` for every agent, and `PATCH /api/settings` accepts it too (same 422 if the version
is not that agent's). Label: "<Agent>: prompt version" / Arabic "إصدار التعليمات".

## For the supervisor task

Read `agents.<slug>.prompt_version` from the effective settings of the active env on each run (it
reloads on `system.settings_changed`). When it is an int, load that `prompt_versions` row's `body`
and use it instead of the files (split nothing: the body is preamble + role already), and record
that version in `agent_runs.prompt_version`. If the row is somehow gone, fall back to the file and
say so — the API refuses unknown versions, but the row could be deleted by hand.

## Not built here

No UI, no change to `AgentRoster` (`/api/agents` still reports the last run's version as before),
and nothing in Python.
