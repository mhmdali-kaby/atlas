# ATL-40 — War Room composer and live updates over Echo

For ATL-42 (Reply, Copy, context menu) and ATL-43 (inspector positions), and anyone who adds a
live event to the SPA. Builds on `docs/handoff/ATL-39-warroom-screen.md` and `ATL-37-bridge.md`.

## The two-minute version

- **One Echo client**: `src/lib/echo.ts` → `getEcho()` (created on first use, never at import, so
  signed-out screens open no socket), `useLiveStatus()` (Echo's `connected | connecting |
  reconnecting | disconnected | failed`), `hasConnected()`, `reconnectEcho()`. Channel auth goes
  through `api.post('/broadcasting/auth')`, so it carries the Sanctum cookie and X-XSRF-TOKEN.
  Config: `VITE_REVERB_APP_KEY|HOST|PORT|SCHEME`, defaults `atlas-local-key`, `127.0.0.1`, `8080`,
  `http` (= backend/.env.example). Listen with a leading dot: `.listen('.warroom.message', …)`.
- **Cache writes**: `features/warroom/live.ts` → `upsertMessage(qc, env, message)` puts a
  `WarroomMessageResource` into `messagesKey(env, thread_id)` by id (replace or sorted insert into
  `pages[0]`) and moves the thread's `last_message_at`. A `thread_id` not in this env's thread list
  invalidates `threadsKey(env)` and is otherwise ignored (that is how another env's broadcast is
  dropped). Use it for anything that adds a message.
- **The outbox** (zustand `useOutbox`) holds the owner's messages that are **sending** or **failed**.
  They are *not* in the query cache (a refetch can't lose a failed one); `WarRoomPage` turns them into
  `LocalMessage`s (`localMessages(...)`) and passes them to `<Feed pending>`, drawn after the stored
  ones. `isLocal(m)` tells them apart; their `id` is a temp id `local-…`, `m.local = { tempId, state }`.
- **Sending**: `sendMessage(qc, { env, threadId, content, replyTo? })` — POST
  `/api/warroom/messages` with `{ env, thread_id, content_md, reply_to }`; the 201 body goes through
  `upsertMessage` and the outbox item is removed. `retryMessage(qc, tempId)` re-posts only a
  `failed` item (a double click sends once). `discardMessage(tempId)` = Delete.
- **ATL-42**: `sendMessage` already takes `replyTo` — the ReplyChip only has to hold the parent id
  and pass it. The composer is `Composer.tsx` (`env`, `thread`, `threadName`, `disabled`), rendered
  in `.wr-pane` after the feed and **keyed by `env:threadId`**; put the chip at the top of `.composer`
  (CSS `.composer-reply` is in the design, not yet in warroom.css). Esc in the textarea closes the
  picker first (it stops propagation); handle Esc-cancels-reply after that. Messages with
  `data-local` (sending/failed) must get no action bar and no menu (design §7).
- **Drafts** survive switching threads: `drafts` map in `live.ts`, key `env:threadId`.

## What the feed does now

- `Feed` takes `pending?: LocalMessage[]`. New ids newer than what was stored before are
  *arrivals*: they fade and rise 8px over 300ms (motion; none under prefers-reduced-motion), once —
  a row scrolled back into view doesn't animate again.
- The owner's own local bubble always pins the feed to the bottom. Someone else's arrival keeps the
  feed pinned only if the reader was at the bottom; otherwise the `.wr-pill` "N new messages ↓"
  counts it (`data-testid="wr-new-pill"`). Scrolled up with nothing new: the round `.wr-latest`
  button. Both live in `.wr-float`, a sibling of the scroller.
- The local bubble swapped for its stored row (201) is not counted or re-animated.

## Live side (`useLiveFeed(env, threadId)` in `useLiveFeed.ts`)

- Subscribes `private-warroom` `.warroom.message` → `upsertMessage`. An arrival from someone else
  (not `author_type: human`) in the open thread is written to the polite live region
  (`data-testid="wr-live"`): `"{name}: {text}"`, text = `plainText` of the body, ≤ 140 chars.
- Banner (`data-testid="wr-banner-down"` / `wr-banner-back`): down = socket not connected after it
  had connected once, or 4 s after mount if it never did. On reconnect: `refetchQueries` for
  `threadsKey(env)` and the **active** `['warroom','messages',env]` queries, then "Live again. N
  missed message(s) caught up." (count of ids new after the refetch) for 2.6 s. "Reconnect now" =
  `reconnectEcho()`.
- Posting works while disconnected (it is plain HTTP).

## Other changes

- `backend/config/cors.php`: `broadcasting/auth` added to `paths` — the SPA (:3000) calls it
  cross-origin with credentials; without it the browser blocks the auth and private channels never
  subscribe. Pest: `BridgeTest` "lets the SPA origin call /broadcasting/auth…".
- `python -m atlas_shared.bus --demo-system-message [--text …] [--env demo|live] [--author <slug>]`
  publishes one `warroom.message` to Main (system event line, or an agent's text with `--author`)
  and prints the envelope id. Uses REDIS_* from the environment.
- e2e: `e2e/support/stack.ts` gives the flows' Laravel `BROADCAST_CONNECTION=reverb` with a test-only
  Reverb app (`REVERB`, port `E2E_REVERB_PORT`, default 8142) and the flows' Vite the matching
  `VITE_REVERB_*` (`SPA_VARS`). The `warroom-live` project (`e2e/flows/warroom.live.spec.ts`) starts
  its own `reverb:start` and `atlas:bridge`, so it can stop Reverb mid-test; it runs **after** the
  four appearance projects because it posts to Main.
- Tokens: `--on-accent-secondary`, `--composer-shadow` (light/dark) in `styles/tokens.css`.
- i18n (`warroom.json`, en + ar): `composer.*`, `send.*`, `live.*`, `newMessages_*` (Arabic has all
  six plural forms), `latest`, `liveAnnounce`.

## Not built here

Reply chip and message actions (ATL-42), the date capsule, slash commands (`/` is sent as text),
`.warroom.thinking` / the WorkingLine, per-env channels, and the composer for the Replay view.
