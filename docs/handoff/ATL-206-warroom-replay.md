# ATL-206 — War Room Replay (for ATL-207, the day line / scrubber)

## Routes and entry points

- `/warroom/replay/:date` — `ReplayPage` (frontend/src/features/warroom/ReplayPage.tsx). `:date` is a Baghdad day `YYYY-MM-DD`.
  Anything that is not a real finished Baghdad day (today, the future, `2026-02-31`, garbage) redirects to yesterday.
- `/warroom/replay` — `ReplayRedirect`, goes to yesterday.
- Live War Room toolbar: a "Replay" link (`data-testid="wr-replay-open"`) to yesterday, portalled into `[data-slot="toolbar-actions"]` beside the inspector toggle.

## Data

`useReplay(env, date)` in `replay.ts` calls the existing API:

```
GET /api/warroom/replay?date=2026-10-28&env=demo
200 { "data": [WarroomMessage, …oldest first, every thread], "meta": { "date": "2026-10-28", "timezone": "Asia/Baghdad", "from": "2026-10-27T21:00:00.000000Z", "to": "2026-10-28T21:00:00.000000Z" } }
```

Query key `['warroom', 'replay', env, date]`, `staleTime: Infinity`, no refetch on focus/reconnect. Because it is not under
`['warroom', 'messages', …]`, the live feed's `upsertMessage` never touches it, and the page subscribes to no socket channel.

Helpers in `replay.ts`: `dayStartMs(day)` (00:00 Baghdad as epoch ms), `shiftDay(day, n)`, `lastReplayDay(now)`,
`isReplayable(day, now)`, `replayPath(day)`, `replayDayName(day, lang, short?)` ("Wednesday 28 October" / "Wed 28 Oct"),
`MOMENT_KINDS` + `isMoment(m)` (watchlist, proposal, order, fill, system_alert, post_mortem — the design's marker kinds).

## Read-only rendering

`readOnly.ts` exports `ReadOnlyContext` (default `false`) and `useReadOnly()`. `ReplayFeed` wraps its rows in
`<ReadOnlyContext.Provider value={true}>`. Inside it:

- `Message`: `canAct` is false → no Reply/Copy bar, no context menu.
- `ProposalCard`: draws `cardRef.snapshot` only (no `/api/proposals/{id}` fetch, ignores a cached live copy), so no Approve/Reject/Edit/Ask,
  no countdown band; shows `pr-readonly` "As posted. Actions are off in Replay."
- Reasoning disclosures are untouched and still open.

## Layout and where the scrubber goes

```
.rp (full-bleed; replay.css adds the shell :has() rules like warroom.css)
  .rp-bar           data-testid="rp-banner"   ← day nav, date popover (rp-date), Back to live (rp-back-live)
  {/* ATL-207 */}   ← the day line goes here, between the bar and the body (design .rp-line)
  .wr.rp-body
    .rp-main        ← ReplayFeed (rp-feed, role=log) + .rp-foot, or loading/empty/error state
    aside.rp-insp   ← "The day": moments list (rp-moment buttons) + first/last message times; hidden below 1280px
```

`ReplayFeed` props: `messages` (already the list to show), `label`, `jump: { id, seq } | null`. A new `jump` scrolls to that
message, flashes it and focuses it (bump `seq` to re-jump to the same id). The feed opens at the end and stays pinned there
while rows measure, until the reader scrolls. For the scrubber, pass `messages.filter(m => Date.parse(m.created_at) <= at)`
and use `jump` for marker clicks; the inspector's moment buttons already call `onPick(id)` in ReplayPage — ATL-207 can
route that through the playhead instead.

Not built here (ATL-207): the day line, play/speed, the "Nothing yet" before-first-message state, the moments'
past/future styling, the inspector toggle/overlay below 1280px, and the sidebar's "Recent days" list.

Strings are under `rp.*` in `src/i18n/locales/{en,ar}/warroom.json`; the design's `rp.timeline`, `rp.play` etc. are not added yet.
