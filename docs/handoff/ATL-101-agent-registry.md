# ATL-101 — Agent registry: how to plug an agent into the supervisor

For ATL-105+ (News Intelligence, Market Scout and every later agent). Decision #247 (1).

`supervisor.py` names no agent. Each agent is one module in
`services/agents/src/atlas_agents/agents/<slug>.py` that registers its hooks on import.
`atlas_agents.registry.discover_hooks()` imports every module in that package with `pkgutil`, so
**adding an agent is adding one file** — do not edit `supervisor.py` or any list.

The agent must still exist in `warroom.agents` / `agents.roster` (config/defaults.yaml) and in
the §7.10 matrix (`atlas_agents/access.py`), and be `enabled`; hooks of a disabled agent are
ignored (no session, no runs).

## Registering

```python
# atlas_agents/agents/news_intelligence.py
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult

SLUG = "news_intelligence"

@HOOKS.mention(SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult | None:
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, host.tool_context(SLUG), request)
    return await host.run(SLUG, build_prompt(request), trigger="mention",
                          task=f"answer {request.author}", correlation_id=request.correlation_id,
                          reviewer=..., max_follow_ups=..., thread_id=thread_id, reply_to=reply_to)

@HOOKS.schedule(SLUG, "news_morning_brief")              # time from agents.schedule.news_morning_brief
async def morning_brief(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    return await host.run(SLUG, "...", trigger="schedule", correlation_id=fire.correlation_id)
```

`atlas_agents/agents/historian.py` is the worked example (mention reply + 07:30 regime table).

## Mention handler

`MentionHandler = Callable[[AgentHost, MentionRequest], Awaitable[RunResult | None]]`

- Called once per `warroom.message` that lists your slug in `mentions`, in this env, for an
  enabled agent, **whoever wrote it** (owner, system, or another agent via `warroom.mention`).
  Never for a message your own agent wrote (no self-mention).
- `MentionRequest` (`atlas_agents.mentions`): `event_id, env, author, author_type, content_md,
  correlation_id, ts, row_id, thread_id, depth`. Owner messages carry `row_id`/`thread_id`; an
  agent's mention carries only its envelope id — `resolve_parent(ctx, request)` looks the row up
  in `warroom_messages` (stored by the bridge) and returns `(reply_to, thread_id)`, or
  `(None, None)` (= Main) if the bridge has not stored it. Same rule for both authors; always
  call it and pass both to `host.run`.
- Return the `RunResult` (it is returned from `handle_envelope`) or None if you started no run.
- Content is untrusted-ish user text: wrap it as the Historian's `mention_prompt` does.

## Schedule hook

`ScheduleRun = Callable[[AgentHost, ScheduleFire], Awaitable[RunResult | None]]`

`@HOOKS.schedule(slug, name, at=None, every_seconds=None)`:

- **Daily** (default): `at="HH:MM"` Asia/Baghdad, or omit `at` and put `name: "HH:MM"` under
  `agents.schedule` in config/defaults.yaml (preferred — owner-configurable). Runs once per Baghdad
  day at that time or the first tick after it (tick every `agents.schedule.tick_seconds`, 30 s).
  "Done today" = an `agent_runs` row with trigger `schedule` and correlation_id
  `fire.correlation_id` (`<name>:<YYYY-MM-DD>`), so **you must pass
  `trigger="schedule", correlation_id=fire.correlation_id` to `host.run`**, or it will re-fire every
  tick. Returning None without a run means "try again next tick".
- **Interval**: `every_seconds=N`. Fires at the first tick, then every N s; tracked in memory (a
  restart fires it again). `fire.correlation_id` = `<name>:<UTC ISO second>`.
- `ScheduleFire`: `name, at (datetime), day (Baghdad date), correlation_id`.
- An exception in a hook is logged and the other hooks still run.

## AgentHost (what the supervisor gives a hook)

`env`, `replaying` (True in evals — don't publish), `tool_context(slug)` (a ToolContext bound to
the same run scope the agent's tools use), `await run(slug, prompt, **kwargs)` (AgentSession.run
kwargs: `trigger, task, correlation_id, routine, reviewer, max_follow_ups, thread_id, reply_to`;
runs of one agent are serialised; never raises except `AgentUnavailableError`), and
`publish(stream, envelope)` (for code-posted messages such as the fallback regime table).

## Chain depth rule (`agents.mentions.max_depth`, default 2)

- A human/system message has depth 0. A run started from a mention of depth `d` runs at depth `d`;
  scheduled and manual runs are depth 0.
- The `warroom.mention` tool stamps `payload.mention_depth = run depth + 1` and the envelope's
  `correlation_id` = the run's correlation_id (so a chain shares one id). An agent message without
  `mention_depth` counts as 1.
- A mention with depth `>= max_depth` starts **no** run; the supervisor logs a warning
  `agents: mention chain stopped at depth N (max_depth M): <author> -> <target> ...`.
- So: owner → A (0) → A mentions B (1, runs) → B mentions C (2, stopped). And for Patch E: the
  08:00 Scout run (depth 0) mentions News/Historian at depth 1 — they run; if News then mentions
  Historian (depth 2) that is stopped. If a Scout-requested brief needs a Historian event study,
  have Scout ask Historian directly, or raise the depth in config.
- Depth is set by the supervisor around `host.run` (a contextvar + the shared `RunScope` on
  ToolContext), not by handlers — nothing to do in your handler.

## Not built here

- No run_id column on the bridge (separate follow-up). No per-chain rate limit beyond depth;
  `agents.news_intelligence.alerts_per_hour` (news.alert trigger) belongs to the News task — a
  `news.alert` envelope is not a `warroom.message` mention, so add that trigger in your module by
  registering it however that task specifies (the supervisor today only dispatches mentions and
  schedule hooks; a new trigger kind would be a small supervisor change).
- `historian.MentionRequest / mention_from_envelope / resolve_parent` still import from
  `atlas_agents.historian` (re-exported) but live in `atlas_agents.mentions`.
