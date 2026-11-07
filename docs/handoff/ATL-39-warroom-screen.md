# ATL-39 — War Room screen: where the composer, Reply and inspector positions plug in

For ATL-40 (composer + Echo), ATL-42 (Reply / actions), ATL-43 (inspector positions).

## The two-minute version

- Feature folder `frontend/src/features/warroom/`. `routes.tsx` registers `area: 'warroom'`, the routes
  `/warroom` (Main) and `/warroom/threads/:threadId`, and two sidebar sections (agents order 10,
  threads order 20). `/` now opens the first area (`HomeRedirect` → `areaHome(shell, areas[0])`),
  which is the War Room; finishing onboarding still lands on `/portfolio`.
- The selected thread comes from the **URL**, not `useParams` (the sidebar is outside the page's
  route): `useSelectedThread()` in `selection.ts` → `{ env, threads, list, selected }`. Main is
  `/warroom`; every other thread is `threadPath(thread)`.
- Data: `api.ts`. TanStack Query keys you must reuse so live messages land in the same cache:
  - threads: `threadsKey(env)` = `['warroom','threads',env]` → `{ data: WarroomThread[] }`
  - messages: `messagesKey(env, threadId)` = `['warroom','messages',env,threadId]`, an
    **infinite query**: `InfiniteData<MessagesPage, string|null>`; `pages[0]` is the **newest** page,
    each page's `data` is newest-first (the API's order). `flattenMessages(data)` gives oldest-first.
  - To add a live/optimistic message (ATL-40): `setQueryData(messagesKey(env, id), d => ({...d, pages: [{...d.pages[0], data: [msg, ...d.pages[0].data]}, ...d.pages.slice(1)]}))`,
    deduping by `id`. Then invalidate/patch `threadsKey(env)` so `last_message_at` moves.
- The feed (`Feed.tsx`) is virtualised with `@tanstack/react-virtual` (dynamic measurement,
  `getItemKey` = message id; rows are a top block, Baghdad day separators `day-YYYY-MM-DD`, and
  messages). It pins to the bottom while the reader is within 48px of it, and keeps the place when
  an older page is prepended. Reaching ≤240px from the top calls `fetchNextPage()`.
- Layout: `WarRoomPage` renders `.wr` = `.wr-pane` (feed) + `Inspector`. The composer goes **inside
  `.wr-pane` after the feed**, absolutely positioned over its bottom edge (design §6: `.composer-wrap`,
  `bottom: 0`). The feed already reserves 148px at the bottom (`paddingEnd: 148`).
- The inspector toggle is portalled into the shell toolbar's `[data-slot="toolbar-actions"]`;
  its state is `localStorage['atlas.wr.inspector']` (`open`/`closed`), from 1280px up; below 1280 it
  opens over the feed.

## For ATL-42 (Reply, Copy, context menu)

- `Feed` already keeps **one `activeId`** (pointermove with the still-pointer check, pointerleave,
  focusin with `:focus-visible`) and renders it as `.msg.is-active`; event lines and alert cards are
  never active (`data-shape` = `agent|owner|event|alert`). Put the action capsule inside `Message`
  for `active` agent/owner messages; CSS for `.msg-actions` is not written yet.
- Roving focus: the scroller is one tab stop; one message has `tabIndex=0` (last focused, else the
  newest); ↑/↓ move and scroll the virtualiser (`focusId`, `pendingFocus`).
- Reply quotes: `Message` gets `parent` (looked up among loaded messages) and `onJump(id)` centres
  and flashes it. A parent not loaded yet shows no quote (the API has no single-message endpoint).
- Excerpts: `<Excerpt text={m.content_md} />` (text.tsx) — the same §9 islands, flat.

## For ATL-43 (positions)

- `Inspector.tsx`: put the Open positions `<section className="insp-sec">` between `{body}` and the
  Thread facts section. `.insp-empty` is already styled for its empty/error states.

## Text rendering (§9)

- `tokens.ts`: `tokenize(src, flat)` (code → status tag → known @mention → instrument ID → figure),
  `parseBlocks`, `excerptSource`, `plainText(src, tagLabel)` (use this for Copy and the live region),
  `around()` to put an island inside a translated sentence (`t(key, { x: '\u0000' })`).
- `text.tsx`: `<Markdown>`, `<Inline>`, `<Excerpt>`, `<InstrumentId>` — all islands are `<bdi dir="ltr">`.
- Agent config: `agents.ts` (`AGENTS`, `agentBySlug`, `agentByHandle`, `agentStatus()` → `'offline'`
  until the runtime). Names/roles/short names are in `warroom.json` under `agents.<slug>`.

## Tests and fixtures

- `WarroomDemoSeeder` (called by `E2eSeeder`): 500 Main messages, newest an owner message at
  `2026-10-28T02:00:00Z` (05:00 Baghdad), a reply, event lines, alerts, a watchlist card; plus empty
  `BTC-USDT-SWAP` and private `historian` threads. Specs that count War Room rows must account for it.
- `e2e/flows/warroom.appearance.spec.ts` (en/ar × light/dark, axe colour-contrast);
  Vitest: `WarRoomPage.test.tsx`, `tokens.test.ts`.

## Not built here

The composer, live Echo updates, the connection banner, the new-messages pill and scroll-to-latest
button, the date capsule, MessageActions/context menu, the AgentCard popover (the agent rows are
already buttons), ShowReasoning, the WorkingLine and inspector positions.
Shell change made here: `--sidebar-width` is now 256 / 236 (design v4 War Room §3).
