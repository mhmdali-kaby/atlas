# ATL-114 — the agents supervisor applies the owner's agent settings live

For ATL-117 (Agents page), ATL-118 (Settings → Agents) and anyone adding an agent.

Code: `services/agents/src/atlas_agents/agent_settings.py` (merge), `settings_applier.py` (reload, enable,
disable, agent_states + `system.agent_state`; the `Supervisor` delegates to it), `supervisor.py`
(`consume_system`, which shares one XREAD loop `_consume_stream` with `consume_warroom`), `session.py` (`AgentSession.update_spec`), `tools/news.py` (web policy per call),
`tools/framework.py` (`ToolContext.web_policy`). Tests: `services/agents/tests/test_agent_settings.py`.

## The two-minute version

- The supervisor's specs = `agents.defaults` + `agents.roster` (config/defaults.yaml) with the env's
  `settings.data` dotted keys from ATL-113 on top. A missing key keeps the default; a wrong type or
  out-of-bounds value is ignored with a warning (Laravel refuses those anyway).
- It reloads on `system.settings_changed` for its env on `atlas.system` (`Supervisor.consume_system`,
  started by `serve()`), and also re-reads `settings.version` before every mention, tick and run, so a
  missed event still applies. One cheap `select version` each time.
- **Disable**: the agent leaves `supervisor.sessions` at once (no new run, `run()` raises
  `AgentUnavailableError`, a mention starts nothing); its CLI session is closed after any in-flight run
  (it waits for the agent lock). An agent already disabled when the supervisor starts gets an
  `agent_states` row of `disabled` (and one `system.agent_state`, reason `settings_disabled`) at start. **Enable**: a new session is built and its prompt version synced.
- **Budgets** (`daily_tokens`, `daily_turns`) apply at the agent's next run: lowering below today's
  usage gives the usual `over_budget` refusal with no CLI call.
- **Model** (tier → alias via `agents.models`) applies at the next connect: the change is held until
  the next run starts, then the open client is disconnected and reconnects with the new model. An
  in-flight run is never touched.
- **Web lists**: `news.fetch_article` reads `agents.web.allow_domains|deny_domains` from the merged
  settings through `ToolContext.web_policy`, for the URL's host and every redirect hop. A context built
  outside the supervisor still uses config/defaults.yaml.
- A replayed day (evals, `replay_as_of`) ignores live settings and runs on the defaults.

## Event for the Agents page

Each enable/disable writes `agent_states` and publishes on `atlas.system`:

```json
{ "type": "system.agent_state", "env": "demo", "source": "agent:market_scout",
  "payload": { "agent": "market_scout", "state": "disabled", "previous_state": "idle",
    "reason": "settings_disabled", "run_id": null, "current_task": null, "last_error": null,
    "resets_at": null, "settings_version": 12 } }
```

Enable is the same with `"state": "idle"`, `"reason": "settings_enabled"`. Same type and fields as the
run-state events, plus `settings_version`. Nothing is published when the state did not change.
