# ATL-93 — Historian agent: @Historian replies, the 07:30 regime table, `run_end`

Code: `services/agents/src/atlas_agents/historian.py` (prompts, card review, regime table),
`supervisor.py` (mention consumer, daily scheduler, per-agent lock), `session.py` (reviewed runs),
`streaming.py` (`run_id`, `run_end`, final-message overrides), `schemas.py` (`CardMeta`, new
`RegimeTable` shape), `__main__.py` (`python -m atlas_agents`). Tests: `agents/tests/test_historian.py`.
This **changes parts of `ATL-88-warroom-events.md`**: read this file where the two disagree.

## The two-minute version

- **One run → at most one message, and always one `run_end`.** Every agent run (any agent, refused
  runs included) ends with a `warroom.thinking` event `delta_type: "run_end"` carrying `run_id` and
  `status` `ok|error|rate_limited|degraded` (`over_budget` maps to `degraded`). Only `ok` runs publish
  a final `warroom.message`; it carries the same `run_id` in its payload and is published *before*
  the `run_end`. Failed runs publish no message.
- **A @Historian answer is one message**: a `research_report` *reply* (same thread as the mention,
  `reply_to` = the mention's row id) whose card is a `HistoryVerdict` plus runtime `meta`. There is no
  separate Main-thread `text` message for that run. It carries `reasoning_md` and `tool_calls` like any
  final message.
- **The card comes from the agent's text**: its answer ends in one fenced ```json block. The runtime
  validates it; an invalid card gets one follow-up query **inside the same run** (same `run_id`, same
  stream; `agents.output_retries`) with the validation errors. Still invalid → run `degraded`, agent
  state `degraded` (`system.agent_state`), no message.
- `meta` is written only by the runtime. An agent passing `meta` through `warroom.post_message` has
  it dropped.
- **Bridge note:** `run_id` is in the published envelope payload, but `atlas:bridge` does not store a
  `run_id` column today, so a message read back from the API has no `run_id`. The live
  `private-warroom` broadcast does carry it. Matching a stored message to its run needs a column
  (bridge/migration task, not built here).

## `warroom.thinking` `run_end` (exact payload)

```json
{"run_id":"5b0e…","agent":"historian","seq":7,"delta_type":"run_end","text":null,"tool":null,
 "tool_use_id":null,"input":null,"output":null,"is_error":null,"truncated":false,
 "status":"rate_limited","error":"rate_limited: Claude usage limit reached"}
```
`status` and `error` exist only on `run_end` (error is `null` for ok). `seq` continues the run's
sequence. A refused run (over budget, inside a known rate-limit window) has a `run_end` and no other
event.

## The @Historian reply (exact `warroom.message`)

```json
{"id":"…","type":"warroom.message","ts":"2026-10-30T09:12:03Z","env":"demo","source":"agent:historian",
 "correlation_id":null,
 "payload":{"thread_id":"<mention's thread uuid>","author_type":"agent","author":"historian",
  "kind":"research_report","content_md":"History supports a bounce: 24h median +1.2%, 61.8% positive.",
  "card":{"type":"history_verdict","question":"BTC after a 3-day 10% drop","inst_id":"BTC-USDT-SWAP",
   "bar":"1H","period":"2024-10-30..2026-10-30","n":12,
   "distribution":{"24h":{"median":0.012,"p10":-0.031,"p90":0.054,"pct_positive":61.8,"n":null}},
   "base_rate":{"24h":{"median":0.002,"p10":-0.035,"p90":0.04,"pct_positive":52.1,"n":null}},
   "regime":null,"stop_noise_check":null,"overfit_warning":false,
   "verdict":"weakly_supports","key_stat":"24h median +1.2% vs base +0.2%; +7.7% best case",
   "data_quality_notes":[],"data_as_of":"2026-10-30T04:00:00Z",
   "meta":{"unmatched_numbers":[{"raw":"+7.7%","value":7.7,"kind":"percent","location":"card",
      "span":[37,42],"path":"key_stat"}],
     "checked":14,
     "verdict_corrections":[{"from":"supports","to":"weakly_supports","rule":"n_below_20","n":12}]}},
  "reply_to":"<mention's row uuid>","mentions":[],"run_id":"5b0e…",
  "reasoning_md":"Find analogs of a 3-day 10% drop.",
  "tool_calls":[{"id":"toolu_1","name":"mcp__history__find_analogs","input":{"inst_id":"BTC-USDT-SWAP","bar":"1H"},
    "input_truncated":false,"as_of":"2026-10-30T04:00:00+00:00","output":"…","output_truncated":false,"is_error":false}],
  "tokens_in":900,"tokens_out":200,"cost_usd":"0.000000"}}
```

- The card validates as `atlas_agents.schemas.HistoryVerdict` **including `meta`** (`CardMeta`).
  Field reference: ATL-88's handoff, `history_verdict` section; `meta` is new.
- `meta.verdict_corrections`: one entry per rule `enforce_verdict_rules` applied, `[]` when the
  verdict was not changed. `rule` is `n_below_<agents.history.verdict.min_n>` (today `n_below_20`) or
  `backtest_on_gapped_data` (a `backtest_rule` result in the run whose `sample.unrepaired_gap` is true).
  `n` is the card's `n`. The UI can write "Capped from Supports: 12 cases" from `from` + `n`.
- `meta.unmatched_numbers` / `checked`: ATL-91's `to_meta()` exactly, run on `content_md` and the card
  (without `meta`) against every tool result of the run plus the mention's own text (so restating the
  asker's "10%" is not flagged).
- `content_md` is the answer with the json block removed. If the agent wrote nothing but the block it
  is `**<verdict>**: <key_stat>`.
- Thread: owner messages carry `message_id` and `thread_id` in their payload, so the reply goes there.
  An agent's mention has only its envelope id; the runtime looks it up in `warroom_messages` and, if
  the bridge has not stored it yet, replies in Main with `"thread":{"kind":"main","key":"main"}` and
  `reply_to: null`.

## The 07:30 regime table (exact `warroom.message`)

```json
{"type":"warroom.message","source":"agent:historian","correlation_id":"historian_regime_table:2026-10-30",
 "payload":{"thread":{"kind":"main","key":"main"},"author_type":"agent","author":"historian",
  "kind":"regime_table","content_md":"<the Historian's commentary, ≤ ~150 words>",
  "card":{"type":"regime_table",
   "rows":[{"inst_id":"BTC-USDT-SWAP","label":"trend_up","probability":0.9312,"days_in_regime":6.0,"method":"hmm"},
           {"inst_id":"XAU-USDT-SWAP","label":"range","probability":null,"days_in_regime":12.0,"method":"fallback"},
           {"inst_id":"NEW-USDT-SWAP","label":"insufficient_data","probability":null,"days_in_regime":null,"method":null}],
   "data_as_of":"2026-10-30T00:00:00Z",
   "meta":{"unmatched_numbers":[],"checked":3,"verdict_corrections":[]}},
  "reply_to":null,"mentions":[],"run_id":"…","reasoning_md":null,"tool_calls":[],
  "tokens_in":50,"tokens_out":10,"cost_usd":"0.000000"}}
```

- Card model: `atlas_agents.schemas.RegimeTable` — **changed from ATL-88**: per-row `data_as_of` is
  gone, rows have `method` (`hmm`|`fallback`|null), and `data_as_of` is on the table (newest bar close
  behind any row).
- Rows are the top `agents.regime_table.top_n` (60) `state=live` instruments of the env by 24h quote
  volume (same basis as `okx_market.list_instruments`), **in that order**; instruments with no
  volume are left out. `label` is `detect_regime`'s (`trend_up`, `trend_down`, `range`, `high_vol`,
  `low_vol`) or `no_data` / `insufficient_data` with the other fields null. Probabilities are rounded
  to 4 places, days to 2.
- The table is computed in code (`historian.build_regime_table`, the same `history.detect_regime`
  tool); the agent only writes `content_md`. If the run fails (rate limit, error, over budget) the
  table is still posted with `content_md` "Regime table: top N … No commentary today (Historian run
  <status>)." and that run's `run_id`; the run's `run_end` still shows the failure.
- Schedule: `agents.schedule.historian_regime_table: "07:30"` Asia/Baghdad; `tick_seconds: 30`. It
  runs once per Baghdad day at the first tick at or after 07:30 (so a process started at 15:00 still
  posts that day's table). "Done" = an `agent_runs` row with agent `historian`, trigger `schedule`,
  correlation_id `historian_regime_table:<YYYY-MM-DD Baghdad>` — any status. A restart the same day
  posts nothing.

## Runtime API added

- `AgentSession.run(..., reviewer=None, max_follow_ups=1)`. A `Reviewer` is
  `async (text_of_this_attempt, all_messages_so_far, attempt) -> Review`;
  `Review(follow_up=str)` asks again in the same run, `Review(message={...})` accepts and overrides the
  final message payload (kind, card, content_md, thread/thread_id, reply_to, mentions),
  `Review(failure=str)` ends the run `degraded`. Usage and turns sum over the run's queries.
  `RunResult` gained `message` and `attempts`; `RunStatus` gained `degraded`.
- `Supervisor(clock=...)`: tool contexts and the schedule use it. `handle_envelope(envelope)`,
  `consume_warroom(redis, stop=, start_id="$", block_ms=)`, `tick(now=None)`, `serve(redis, stop=)`.
  One `asyncio.Lock` per agent: runs of one agent never overlap.
- Mentions: only the Historian has a mention handler. A message counts when its `mentions` list holds
  the slug, its env is the supervisor's, and it is not written by that agent. The consumer starts at
  the stream's newest entry (`$`), so mentions sent while the process was down are not answered.
- Process: `uv run --package atlas-agents python -m atlas_agents [--env demo|live]` (default
  `ATLAS_ENV` or `env.default`). `./atlas up` does not start it yet (DevOps).

## Not built

No `run_id` column on `warroom_messages`; no answering of mentions missed while down; no handlers for
the other eight agents; the Historian prompt file is unchanged (the output format is in the per-run
prompt, `historian.mention_prompt`).
