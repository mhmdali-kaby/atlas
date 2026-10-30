# ATL-89: the bridge and the War Room API for agent output

Code: `backend/app/Support/Events/BridgeEventHandler.php`, `backend/app/Http/Resources/WarroomMessageResource.php`.
Tests: `backend/tests/Feature/Warroom/AgentOutputBridgeTest.php`.

## The two-minute version

- An agent `warroom.message` (ATL-88 shape) is stored with `kind`, `card`, `reasoning_md`, `tool_calls`,
  `tokens_in`, `tokens_out`, `cost_usd` exactly as published. The checks are unchanged: `source` must be
  `agent:<slug>` with `author` = that slug (a known agent), `author_type: human` is always refused, and an
  envelope id is stored once (a replay returns `duplicate` and broadcasts nothing).
- `warroom.thinking` is never stored. It is broadcast on `private-warroom`, event name `warroom.thinking`,
  with the **whole envelope** as the broadcast payload (so the run fields are under `.payload`). Only its
  size changes: see the cap below.
- "Show reasoning" on a stored message reads the message's `reasoning_md` and `tool_calls`. The stream is a
  live preview only.

## Message JSON (GET /api/warroom/messages `data[]`, and the `warroom.message` broadcast)

```json
{"id":"…","thread_id":"…","author_type":"agent","author":"historian","kind":"research_report",
 "content_md":"History supports …","card":{"type":"history_verdict","…":"…","meta":{"numguard":{"unmatched_numbers":[…],"checked":16}}},
 "reply_to":null,"mentions":[],"reasoning_md":"Regime first.\n\nThen analogs.",
 "tool_calls":[{"id":"toolu_1","name":"mcp__history__detect_regime","input":{…},"input_truncated":false,
   "as_of":"…","output":"…","output_truncated":false,"is_error":null}],
 "tokens_in":5120,"tokens_out":812,"cost_usd":"0.041300",
 "unmatched_numbers":[{"raw":"0.0731","value":0.0731,"kind":"number","location":"card","span":null,"path":"distribution.24h.p90"}],
 "created_at":"2026-10-29T09:00:00.000000Z"}
```

`unmatched_numbers` is new. It is the ATL-91 `to_meta()` list read from `card.meta.numguard.unmatched_numbers`,
or else `card.meta.unmatched_numbers`. It is `null` when the card has no such meta (owner, system and
plain text messages). **ATL-93: put `to_meta()` at `card.meta.numguard`** for it to show. A message
without a card has nowhere to carry it today.

## The thinking cap

`warroom.thinking_max_bytes` in `config/defaults.yaml` (default 16384). If `json_encode(payload)` is
longer, the bridge sets `truncated: true` and cuts the longest string fields (`text`, `output`, and
`input`, which is first turned into its JSON string if it is an object) at a UTF-8 boundary until it
fits. `run_id`, `agent`, `seq`, `delta_type`, `tool`, `tool_use_id` are never cut, and every key stays.
If it still cannot fit, only `{truncated, run_id, agent, seq, delta_type}` is sent. A payload that fits is
sent byte-for-byte unchanged. The UI must accept `input` as a string when `truncated` is true.
