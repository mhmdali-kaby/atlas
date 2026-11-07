# ATL-97: "Show reasoning" and live run rows in the War Room

Code: `frontend/src/features/warroom/runs.ts` (live-run store + pure stream reducer),
`Reasoning.tsx` (the disclosure, shared by stored and live), `LiveRunRow.tsx` (the message being
written), wiring in `useLiveFeed.ts`, `Feed.tsx`, `WarRoomPage.tsx`, `Message.tsx`.
Tests: `Reasoning.test.tsx` (Vitest), `e2e/warroom-reasoning.spec.ts` (mocked Playwright, en/ar ×
light/dark at 1280 px; screenshots in `evidence/ATL-97/`).

## The two-minute version

- Every agent message with `reasoning_md` or `tool_calls` gets a collapsed "Show reasoning"
  (Radix Collapsible). Each tool call is its own collapsible row with pretty JSON input/output,
  trimmed past 8 lines; `*_truncated` adds the ✂ caption. The cost line is notional (decision #242).
- `.warroom.thinking` on `private-warroom` (the whole ATL-88 envelope) feeds `useRuns.apply`.
  Events for another env are ignored; switching env clears the runs. One row per `run_id`,
  placed by `payload.thread_id` (null → Main) and sorted into the feed by the first event's `ts`,
  under the quote of `reply_to`.
- **Matching the final message to its row**: `message.run_id` when present, otherwise the same
  agent + thread + reply_to. The API resource and the bridge do not carry `run_id` today (ATL-93
  note), so the fallback is what runs in practice. Message-then-`run_end` and the reverse both work;
  a run resolved by its message drops its later events. A `run_end ok` whose message never arrives
  is removed after 15 s.
- `run_end` with `error | rate_limited | degraded` stops the row with the reason; "Ask again"
  (error, answering an owner message) re-posts the question. Stopped rows are never stored.
- Open state for panels and tool rows lives in `useRuns.open` (`<messageId>` or `run:<runId>`, tool
  rows `<panel>:<index>`), so it survives virtualisation and carries from the live row to the
  message; focus on the row or its toggle moves to the message or its toggle.

## Not built here

- No live-row screenshot in Playwright: the mocked suite has no socket. The live path is covered
  in Vitest with a socket double.
- The sidebar's "Degraded: usage limit" status for a rate-limited agent (agent state) is not part
  of this task.
