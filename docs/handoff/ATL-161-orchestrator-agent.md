# ATL-161 — the Orchestrator agent (brief §7.9)

For ATL-163 (slash commands), ATL-164/ATL-180 (War Room cards), ATL-169 (Portfolio Manager), ATL-182
(voter prompts) and anyone rendering the Orchestrator's messages.

## Two-minute version

- The Orchestrator is **enabled by default** now (`agents.roster.orchestrator.enabled: true`). That
  also switches on ATL-160's analysis cycles.
- Owner War Room messages with **no @mention** (hook `warroom.message.text`), and owner messages that
  **@Orch**, go to it. Agent mentions of the Orchestrator start nothing.
- `/why <id>` is the Orchestrator's. Every other message starting with `/` is ignored here — ATL-163
  must handle `/pause`, `/cycle now`, … itself (e.g. publish `orchestrator.cycle_now`).
- Limit changes and approvals never reach the model: code answers them. Everything else: code
  acknowledges at once, then a background model run routes it with an assignment.
- Nothing here votes, proposes, approves, or writes settings.

## Owner message flow (`atlas_agents/orchestrator/classify.py` → `owner.py`; `/why` in `why.py`)

1. `classify.classify(content)` → `instruction | question | approval | limit_change_request | chat`.
   `limit_change_request` (a change verb or a number with x/%/USDT + a limit keyword) and `approval`
   are decisive. A question (trailing ?/؟ or a question word first) is never an approval, and is a
   limit change only when a change verb is followed by a value ("can you set leverage to 20x?");
   "What leverage do we use?" and "Why did the Risk Engine not approve it?" are questions and go to
   the model. `LimitRef(setting, section)` names the Settings page, e.g. leverage →
   `("Max leverage", "Risk")`.
2. Code posts one message immediately, `reply_to` = the owner's row id, same thread:

```json
{"author_type": "agent", "author": "orchestrator", "kind": "text", "thread_id": "<owner thread>",
 "reply_to": "<owner message row id>",
 "content_md": "Got it — instruction: \"Have the Historian check XAU after CPI\". Routing it now.",
 "card": {"type": "owner_ack", "classification": "instruction", "ack_latency_seconds": 0.412}}
```

   Card types: `owner_ack`, `owner_limit_answer` (+ `setting`, `settings_section`),
   `owner_approval_answer`. The limit text is
   `Only the owner can change that in Settings → Risk (Max leverage). Agents cannot change limits, so nothing was changed.`
3. Non-decisive kinds: a model run (trigger `mention`) answers
   `{"kind", "reply_md", "assign": {"agent", "task", "deadline_minutes"} | null}`. Its final message
   (posted by the session) is `card.type = "owner_routing"` with `classification`,
   `code_classification`, `assign`. Then code publishes the assignment:

```json
{"author": "orchestrator", "kind": "text", "thread_id": "<owner thread>", "reply_to": "<owner row id>",
 "mentions": ["historian"], "mention_depth": 1,
 "content_md": "@Historian Event study: XAU around US CPI. (by 11:20 Baghdad, 2026-11-01T08:20:00Z)",
 "card": {"type": "assignment", "agent": "historian", "task": "Event study: XAU around US CPI.",
          "deadline": "2026-11-01T08:20:00Z"}}
```

   Invalid JSON twice → fallback: one named agent in the message → assigned the instruction by code;
   otherwise a line asking the owner to @mention the agent.
4. Ack latency per Baghdad day: `agent_scores` row agent `orchestrator`; `n` = acks, `hit_rate` = share
   ≤ `ack_target_seconds` (10); `data` = `ack_latency_seconds[]`, `ack_median_seconds`,
   `ack_p90_seconds`, `ack_max_seconds`, `ack_target_seconds`, `loops_stopped`.

## `/why <proposal id | order id | clOrdId | ordId>`

Reads the proposal chain (or the order's proposal), its votes, risk checks and up to 12 War Room
messages whose `card.proposal_id` is in the chain. The answer (model, fallback = code summary) is
cut to fewer than 150 words including a links line, `Cards: [author card type](/warroom/threads/<thread>#m-<message id>) · …`
(newest 4). Card: `{"type": "why_answer", "proposal_id", "order_id", "links": [href…]}`. The
`#m-<id>` anchor is a convention the frontend does not scroll to yet.

## Loop stop (`loops.py`) and the early vote

Agent replies (`reply_to` set) of kind `text` or `research_report` are tracked per thread. More than
`loop_max_replies` (3) alternating between the same two agents → one message, same thread:

```json
{"author": "orchestrator", "kind": "text",
 "content_md": "Loop stopped: @Strategist and @Devil exchanged 4 replies on proposal <id>. Strategist: \"…\" Devil's Advocate: \"…\" Calling the vote now.",
 "card": {"type": "loop_summary", "proposal_id": "<id>", "agents": ["devils_advocate", "strategist"],
          "replies": 4, "vote_called": true}}
```

The proposal id comes from a card in the replies or from walking `reply_to` up in Postgres.
`cycle.call_vote(env, proposal_id)` makes a running cycle stop waiting for the DA review and open
the vote; `cycle.state_changed` for `reviews_done` now carries `vote_called: [ids]`. An owner message
in the thread resets the count.

## "Where we are" (`status.py`)

Schedule hook `where_we_are` (every 60 s) posts at 08:10, 10:10, … 22:10 Baghdad, Main thread, never
after 23:00, once per slot:

```json
{"author": "orchestrator", "kind": "text", "thread": {"kind": "main", "key": "main"},
 "content_md": "Where we are (10:10 Baghdad): watchlist 2 (BTC-USDT-SWAP, XAU-USDT-SWAP); 0 open positions; PnL today +$120.50 (+0.01%); daily loss budget left $200,000.00, 6 position slot(s) left; next: analysis cycle at 10:40.",
 "card": {"type": "where_we_are", "slot_at": "2026-11-01T07:10:00Z",
          "watchlist": {"status": "published", "inst_ids": ["…"]}, "open_positions": [{"inst_id", "pos_side", "upl"}],
          "pnl_today_usd": "120.50", "pnl_today_pct": "0.01",
          "budget": {"daily_loss_left_usd": "200000", "positions_left": 6},
          "cycle_state": null, "next_step": "analysis cycle at 10:40", "as_of": "…Z"}}
```

## Tools (`atlas_agents/tools/orchestration.py`, Orchestrator only)

`start_cycle(kind="manual")` publishes `orchestrator.cycle_now` (refused while a cycle runs);
`assign(agent, task, deadline)`; `set_agent_state(agent, paused|active)` (writes `agent_states`,
publishes `system.agent_state`; active only from paused); `summarise_thread(thread_id)`. §7.9's
`open_vote`/`close_vote` were removed from the catalogue: the vote is code.

## Not built

- `request_human_input` is still the Phase-1 stub.
- "Ask the team" (ATL-162) reaches the Orchestrator as an @Orch owner message; the routing prompt
  does not include the proposal card it came from (MentionRequest carries no card).
- Pausing an agent is honoured by the cycle only; an @mention still runs a paused agent.
- No "chase late agents once" message and no 3-line cycle summary from the model (ATL-160's code
  lines cover the cycle).
