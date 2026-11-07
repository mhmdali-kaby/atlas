# ATL-237 — trading_core and the agents follow the active env

Built on ATL-236 (`system_state.active_env`, `system.env_changed`). For ATL-238, ATL-239 and anyone
who touches trading_core's app wiring or the agents supervisor.

## Reading the env (Python)

`atlas_shared.active_env`:

- `stored_env(engine) -> str | None` — `SELECT active_env FROM system_state WHERE id = 1`; None when the
  row or the table is missing; raises SQLAlchemyError when Postgres is unreachable.
- `read_active_env(engine, fallback=None) -> str` — the row, else `fallback`
  (default `fallback_env()` = `ATLAS_ENV` → `ATLAS_DEFAULT_ENV` → `env.default`). A DB error also
  falls back.
- `ENV_CHANGED = "system.env_changed"`.

**The row decides, not the event payload.** Both services treat `system.env_changed` as "re-read
system_state now". They also re-read it on a timer, in case the event was missed (Redis down):
trading_core every `env.watch_poll_seconds` (30, config/defaults.yaml), the agents on every schedule
tick.

## trading_core

`trading_core/runtime.py`:

- `EnvRuntime` holds everything bound to one env: scheduler (reconcile, instruments, account, bills,
  history, backfill, pollers, quality, approvals, lifecycle sweep), market feed, private feed, entry
  lifecycle, breaker monitor and the reconcile gate. `install(app.state)` points `app.state.runtime`,
  `.scheduler`, `.reconcile_gate`, `.market_feed`, `.private_feed`, `.breaker_monitor`,
  `.entry_lifecycle` at it.
- `EnvSwitcher` (`app.state.env_switcher`) does the switch: a closed gate with reason `reconciling`
  first, then `old.stop()`, then `build_runtime(settings with the new env)`, then `install` + `start`.
  The reconcile job runs first in the new scheduler and opens the gate when it finishes.
- `app.build_runtime(settings, engine, …)` is what `create_app` used to do inline. `create_app` gained
  `runtime_factory=` (tests) and `follow_env=` (default: on for the real app, off around an injected
  scheduler). At startup (lifespan, `run_scheduler` on) the stored env wins over ATLAS_ENV.

Routes must read `request.app.state.settings.env` / `.scheduler` / `.reconcile_gate` per request —
never capture them in a closure at `create_app` time, or they keep the old env after a switch.
`/health` now reports the live value.

### The refusal while reconciling

`POST /internal/execute/{risk_check_id}` and `POST /internal/proposals/{id}/execute` answer 409:

```json
{"ok": false, "status": "refused", "reason": "reconciling",
 "detail": "Atlas is reconciling with OKX after switching environment; try again shortly.",
 "risk_check_id": "…"}
```

After a restart the same body has `"reason": "not_reconciled"` (unchanged). Laravel/the SPA can map
`reconciling` to its own message; it is not translated here.

## Agents supervisor

- `python -m atlas_agents` starts in `system_state.active_env` (ATLAS_ENV fallback). `--env` still
  overrides when given. `atlas_agents.__main__.startup_env(engine, explicit)`.
- `Supervisor.handle_system_envelope` follows `system.env_changed`; `Supervisor.switch_env(target)` /
  `follow_stored_env()` reload the settings row, prompt versions and sessions for the new env (a new
  `SettingsApplier`). New sessions, their tools (`ToolContext.env`) and the preamble
  (`Environment: live`) use the new env.
- A run already in flight keeps its old session (old env) and finishes; that session is closed after
  it, and `settled()` waits for it. Interval hooks fire again at the first tick in the new env; daily
  jobs are "done" per env (`agent_runs.env`).

## Not changed

Ingest (env-independent). No order path: Executor ← Risk Engine only. Trading stays paused after a
switch (ATL-236 pauses the new env); resuming is the owner's action.
