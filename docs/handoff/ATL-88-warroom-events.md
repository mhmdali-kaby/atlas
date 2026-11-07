# ATL-88: warroom tools, `warroom.thinking` and the final run message

Modules: `services/agents/src/atlas_agents/tools/warroom.py` (MCP tools),
`services/agents/src/atlas_agents/streaming.py` (`ThinkingPublisher`),
`services/agents/src/atlas_agents/schemas.py` (ATL-87's single `HistoryVerdict`, plus `RegimeTable`,
`CARD_TYPES`, `card_rule_violations`).
Tests: `agents/tests/test_warroom_tools.py`, `agents/tests/test_streaming.py`.

## The two-minute version

- Agents never write Postgres for the War Room. Each tool publishes one `warroom.message` on
  `atlas.warroom` through `ctx.publish`, with source `agent:<slug>` and payload author `<slug>`.
  `atlas:bridge` checks the pair, stores the row and broadcasts it. I checked every envelope below
  against the real `BridgeEventHandler`: messages come back `persisted`, thinking events come back
  `broadcast`.
- `warroom.thinking` is never stored. The bridge rebroadcasts it as-is on `private-warroom`, event
  name `warroom.thinking`, with the whole envelope as the payload. The UI should treat it as a live
  preview only. The **final `warroom.message` of a run** carries the full record (`reasoning_md`,
  `tool_calls`, tokens, cost), so "Show reasoning" on a stored message must read those columns,
  not the stream.
- Events are throttled per run to `agents.streaming.max_events_per_second` (10). They are spaced at
  least 0.1 s apart, and deltas of one type that arrive in between are coalesced into one event.
  `seq` starts at 1 and rises by 1 per event within a run. Order the UI by `(run_id, seq)`.
- The Supervisor attaches the publisher (and sets `include_partial_messages=True`) only for live
  runs. With `replay_as_of` set (evals) nothing streams. The warroom tools refuse in replay
  (`not_available_in_replay`) unless the `ToolContext` was given a publisher other than the real bus.
- A run's final message is the run's assistant text (`RunResult.text`) in the Main thread, kind
  `text`. If an agent also called `post_message` during the run, both appear. That is deliberate
  for now; a later task can choose to suppress or thread it. A run with no text, no thinking and no
  tool call posts no final message. A run with thinking or tool calls but no text posts
  `Run finished without a message.`

## Tools (the model sees `{as_of, source: "atlas.warroom", truncated, data}`)

| tool | input | publishes | errors |
| --- | --- | --- | --- |
| `post_message` | `kind` ∈ text, watchlist, research_report, news_alert, vote_summary, post_mortem, system_alert; `content_md`; `card?` | Main thread, `mentions: []` | `invalid_input` (other kinds), `empty_content`, `content_too_long` (> `warroom.content_max_chars` = 4000; refused, not cut), `invalid_card` |
| `reply` | `message_id` (row id **or** the envelope id `post_message` returned), `content_md` | parent's thread (`thread_id`), `reply_to` = parent **row id**, kind `text` | `unknown_message` (not in this env, not yet stored by the bridge, or created after the replay cutoff) |
| `mention` | `agent` (slug from `warroom.agents`), `request` | Main, kind `text`, `content_md = "@<Handle> <request>"`, `mentions: [<slug>]` | `unknown_agent` (unknown slug or itself) |
| `cast_vote`, `submit_proposal`, `request_human_input` | catalogue inputs | nothing | `not_available_in_phase_1` |

Successful `data`:
```json
{"message_id":"9eef3c0f-0e65-4f89-b794-cb17a3631cbb","kind":"text","thread":{"kind":"main","key":"main"},
 "reply_to":null,"mentions":["historian"],"published_at":"2026-10-29T09:00:00Z"}
```
For a reply, `thread` is `{"thread_id": "<uuid>"}`.

### Cards

If `card.type` names a schema in `atlas_agents.schemas.CARD_TYPES`, the card must validate against
it and its kind must allow that type (`KIND_CARD_TYPES`). Today `research_report` allows
`history_verdict` and `regime_table`. Validation errors go back to the agent as `invalid_card`,
each one written `loc: msg`. A card with no `type`, or a type that is not registered, is stored
as given. Register TechnicalSetup and the other schemas there when they exist.

- `history_verdict` (brief §7.3, the same model ATL-87's tools and `enforce_verdict_rules` use):
  `question, inst_id|null, bar|null, period|null, n, distribution{horizon: {median,p10,p90,
  pct_positive 0-100, n?}}, base_rate{same}, regime?{label, probability 0-1|null,
  days_in_regime|null}, stop_noise_check?{stop_distance_atr, median_4h_range_atr,
  verdict ok|inside_noise}, overfit_warning, verdict (supports|weakly_supports|does_not_support|
  contradicts), key_stat, data_quality_notes[], data_as_of (tz-aware)`. Extra keys are refused,
  p10 ≤ median ≤ p90 must hold, and a card that `enforce_verdict_rules` would still correct is
  refused (e.g. **n < `agents.history.verdict.min_n` with `supports`**), so run it before posting.
- `regime_table`: `rows[] (≥1) of {inst_id, label, probability 0-1|null, days_in_regime ≥0|null, data_as_of}`.

## `warroom.message` from an agent (exact envelope)

```json
{"id":"84b847dd-e04a-4237-bc09-7aeff7727b9c","type":"warroom.message","ts":"2026-10-29T09:00:00Z",
 "env":"demo","source":"agent:historian","correlation_id":"cycle-2026-10-29",
 "payload":{"thread":{"kind":"main","key":"main"},
  "author_type":"agent","author":"historian","kind":"research_report",
  "content_md":"Regimes","card":{"type":"regime_table","rows":[{"inst_id":"BTC-USDT-SWAP","label":"trend",
   "probability":0.8,"days_in_regime":3,"data_as_of":"2026-10-29T04:00:00Z"}]},
  "reply_to":null,"mentions":[],"reasoning_md":null,"tool_calls":null,
  "tokens_in":null,"tokens_out":null,"cost_usd":null}}
```
A reply has `"thread_id":"<uuid>"` instead of `"thread"`, plus `"reply_to":"<row uuid>"`.
`ts` is `ctx.now()`, which is the replay instant in replay. `correlation_id` is the session's
`ToolContext.correlation_id`.

### The run's final message

Same shape. Kind is `text`, thread Main, and it is filled in:
```json
"payload":{"thread":{"kind":"main","key":"main"},"author_type":"agent","author":"historian","kind":"text",
 "content_md":"Done.","card":null,"reply_to":null,"mentions":[],
 "reasoning_md":"thinking...",
 "tool_calls":[{"id":"toolu_1","name":"mcp__history__detect_regime","input":{"inst_id":"XAU-USDT-SWAP"},
   "input_truncated":false,"as_of":"2026-10-29T07:00:00+00:00",
   "output":"{\"as_of\":\"2026-10-29T07:00:00+00:00\",\"source\":\"x\",\"data\":{}}",
   "output_truncated":false,"is_error":null}],
 "tokens_in":5,"tokens_out":2,"cost_usd":"0.010000"}
```
- `reasoning_md`: all thinking in arrival order. Streamed deltas are concatenated as they arrive.
  Separate thinking blocks are joined with a blank line. `null` when there was no thinking.
- `tool_calls[]`: one entry per `ToolUseBlock`, in call order. `input` is the JSON object, or, when
  its JSON exceeds `tool_input_max_chars` (2000), a string cut to that length with
  `input_truncated: true`. `output` is the tool result text cut to `tool_output_max_chars` (2000).
  `as_of` is read from the Atlas tool envelope; it is `null` for errors and built-ins. Tool names
  are the CLI's fully qualified ones (`mcp__<server>__<tool>`).
- `content_md` is cut to 4000 characters with `…`. `cost_usd` is the SDK's notional cost as a
  decimal string with 6 places (decision #242: notional only). `tokens_in` includes cache tokens,
  the same as `agent_runs`.

## `warroom.thinking` (exact envelope; broadcast only)

```json
{"id":"…","type":"warroom.thinking","ts":"2026-10-29T09:00:01.2Z","env":"demo",
 "source":"agent:historian","correlation_id":"cycle-1",
 "payload":{"run_id":"<agent_runs.id>","agent":"historian","seq":3,
  "delta_type":"thinking|text|tool_use|tool_result|run_end",
  "thread_id":"<warroom_threads.id>|null","reply_to":"<warroom_messages.id>|null",
  "text":"… (thinking/text only, cut to event_text_max_chars 4000)",
  "tool":"mcp__history__detect_regime (tool_use/tool_result)",
  "tool_use_id":"toolu_1 (tool_use/tool_result)",
  "input":{"inst_id":"XAU-USDT-SWAP"},
  "output":"… (tool_result only, cut)",
  "is_error":null,
  "truncated":false}}
```
Every key is always present. Fields that do not apply to the `delta_type` are `null`. `run_id`
is the `agent_runs` row id, so the UI can link a live stream to the run, and the final message is
published right after the stream's last event. `text` events are the agent's visible answer as it
is written. The final message repeats it in full.

- `thread_id`/`reply_to` (ATL-98): for a run started from an @mention, the mention's own
  `thread_id` and message id (`reply_to`) — the exact values the run's final reply uses
  (`AgentSession.run(..., thread_id=, reply_to=)`, passed by the Historian's mention handler
  once it has resolved the parent with `historian.resolve_parent`). Both are `null` for a
  scheduled run (the 07:30 regime table) or a manual run, and the UI shows those in Main. They
  are set once, at observer creation, so every event of the run carries them — not only the
  final message, which is published after the stream. `BridgeEventHandler::capPayload` never
  truncates or drops them: they are excluded from the string fields it cuts and are kept in the
  last-resort identifying-keys-only shape alongside `run_id`/`agent`/`seq`/`delta_type`.

## Config (`config/defaults.yaml`, `agents.streaming`)

`max_events_per_second: 10`, `event_text_max_chars: 4000`, `tool_input_max_chars: 2000`,
`tool_output_max_chars: 2000`. The content cap is the existing `warroom.content_max_chars`.

## Runtime hooks

- `AgentSession(..., observer_factory=fn(run_id, correlation_id, thread_id, reply_to) ->
  RunObserver)`. The last two (ATL-98) are `AgentSession.run(..., thread_id=, reply_to=)`'s,
  defaulting to `None`. A `RunObserver` has `async on_message(msg)` for every SDK message and
  `async on_finish(RunResult)`. Observer failures are logged and never fail the run, and publish
  failures are swallowed the same way.
- `streaming.observer_factory(agent=, env=, publish=)` builds that from `ThinkingPublisher`.
- `atlas_agents.testing` adds `thinking(text)`, `thinking_delta(text)`, `text_delta(text)` and
  `block_stop()` builders for FakeClaudeClient scripts.

## Not built

There is no `warroom` read tool, so an agent learns message ids from its prompt or from its own
`post_message` result. Mentions do not route yet: that is the supervisor's `atlas.warroom`
consumer. The final message does not reply to the triggering mention. There is no per-run
correlation id beyond the session's. Votes, proposals and owner questions are Phase 2.
