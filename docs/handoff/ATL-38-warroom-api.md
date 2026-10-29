# ATL-38 — War Room API

For the War Room UI (ATL-39/40) and the bridge (ATL-37).

## The two-minute version

- Five endpoints under `/api/warroom`, all `auth:sanctum` (SPA cookie session + `X-XSRF-TOKEN`), all
  401 when signed out. Routes: `backend/routes/api/warroom.php`; controller
  `App\Http\Controllers\Warroom\WarroomController`.
- **Env**: there is no "active env" column yet, so every endpoint takes `env=demo|live` (query string, or
  in the POST body) and defaults to `atlas_defaults.env.default` (`demo`) — the same rule as Settings and
  Portfolio. Send `env` explicitly once the SPA knows it.
- Messages are rendered with `WarroomMessageResource` everywhere — the same shape the bridge broadcasts.
- Owner posts are **persisted by Laravel first**, then published on `atlas.warroom` with envelope
  `id` = the row's `event_id`. The bridge must dedupe on `event_id` (unique column) and broadcast; it must
  not insert a second row. The payload also carries `message_id` and `created_at`, so the bridge can
  broadcast without re-reading if it wants.
- The nine agents live in `config/defaults.yaml` → `warroom.agents` as
  `{name, handle, aliases}` per slug. `App\Support\Warroom\MentionParser` resolves the slug, the name,
  the one-word handle and the aliases, case-insensitively, ignoring punctuation (`@Historian`,
  `@MarketScout`, `@market_scout`, `@Scout`, `@Devil`, `@PM`). The composer autocompletes from
  `GET /api/warroom/agents` and should insert `@<handle>`.
- `EventBus::publish()` gained an optional trailing `id:` argument (envelope id). Existing callers are
  unchanged.

## GET /api/warroom/agents

The nine agents in brief §7 order. Static config, no `env`. Insert `@` + `handle` in the composer;
`slug` is what appears in a message's `mentions`; `name` is display copy (translate by `slug` in the SPA
if you need Arabic).

```json
{
  "data": [
    { "slug": "orchestrator", "name": "Orchestrator", "handle": "Orch", "aliases": [] },
    { "slug": "market_scout", "name": "Market Scout", "handle": "Scout", "aliases": [] },
    { "slug": "news_intelligence", "name": "News Intelligence", "handle": "News", "aliases": ["newsintel"] },
    { "slug": "historian", "name": "Historian", "handle": "Historian", "aliases": [] },
    { "slug": "technical_analyst", "name": "Technical Analyst", "handle": "Technical", "aliases": ["ta"] },
    { "slug": "derivatives_sentiment", "name": "Derivatives & Sentiment", "handle": "Derivatives", "aliases": ["sentiment"] },
    { "slug": "strategist", "name": "Strategist", "handle": "Strategist", "aliases": [] },
    { "slug": "devils_advocate", "name": "Devil's Advocate", "handle": "Devil", "aliases": ["da"] },
    { "slug": "portfolio_manager", "name": "Portfolio Manager", "handle": "PM", "aliases": ["portfolio"] }
  ]
}
```

## GET /api/warroom/threads?env=demo

Threads of the env. `main` first, then by newest message (empty threads last), then title.
`last_message_at` is the newest message's `created_at` (null when empty). No unread state.

```json
{
  "data": [
    { "id": "9d4c1b7e-2f0a-4c1e-8a55-0f6a3c2e1b10", "kind": "main", "key": "main", "title": "Main",
      "last_message_at": "2026-10-29T05:00:00.123456Z", "created_at": "2026-10-28T08:00:00.000000Z" },
    { "id": "a1e0…", "kind": "instrument", "key": "BTC-USDT-SWAP", "title": "BTC-USDT-SWAP",
      "last_message_at": "2026-10-28T08:00:00.500000Z", "created_at": "2026-10-28T08:00:00.000000Z" },
    { "id": "b7f2…", "kind": "agent", "key": "historian", "title": "historian",
      "last_message_at": null, "created_at": "2026-10-28T08:00:00.000000Z" }
  ]
}
```

`title` for the main thread is the seeded `Main`; translate by `kind`/`key` in the SPA.

## GET /api/warroom/messages?thread_id=&cursor=&limit=&env=

- `thread_id` required, must be a thread of the env (else 422).
- `limit` 1..100 (default 50; 101 → 422).
- Newest first, cursor-paged on `(created_at desc, id desc)` — stable when two rows share a timestamp.
- Next page: pass `meta.next_cursor` back as `cursor`. `null` = no older messages.

```json
{
  "data": [
    {
      "id": "5c0e8f9a-3b1d-4d2e-9f60-7a8b9c0d1e2f",
      "thread_id": "9d4c1b7e-2f0a-4c1e-8a55-0f6a3c2e1b10",
      "author_type": "human",
      "author": "Mhmd",
      "kind": "text",
      "content_md": "@Historian hi",
      "card": null,
      "reply_to": null,
      "mentions": ["historian"],
      "reasoning_md": null,
      "tool_calls": null,
      "tokens_in": null,
      "tokens_out": null,
      "cost_usd": null,
      "created_at": "2026-10-29T05:00:00.123456Z"
    }
  ],
  "links": {
    "first": null,
    "last": null,
    "prev": null,
    "next": "http://localhost:8000/api/warroom/messages?thread_id=9d4c…&limit=100&cursor=eyJjcmVhdGVkX2F0Ijoi…"
  },
  "meta": {
    "path": "http://localhost:8000/api/warroom/messages",
    "per_page": 100,
    "next_cursor": "eyJjcmVhdGVkX2F0Ijoi…",
    "prev_cursor": null
  }
}
```

Agent rows have the same keys; `tokens_*` are ints and `cost_usd` a decimal string (`"0.012300"`).
Human rows always have `tokens_in`, `tokens_out`, `cost_usd` = null.

## POST /api/warroom/messages

Throttled 30/min per user (`throttle:warroom-post`, number from `warroom.post_per_minute`) → 429.

Request:

```json
{
  "thread_id": "9d4c1b7e-2f0a-4c1e-8a55-0f6a3c2e1b10",
  "content_md": "@Historian what usually happens after CPI?",
  "reply_to": null,
  "env": "demo"
}
```

- `content_md` 1..4000 chars after trimming (empty/whitespace → 422 "required"; 4001 → 422 "max").
- `reply_to` optional/nullable; must be a message in the same thread and env, else 422 on `reply_to`.
- Stored as `author_type: human`, `author`: the user's `name`, `kind: text`, `mentions` parsed.
- One `audit_log` row: action `warroom.message_posted`, subject `warroom_messages`/<id>, `after` =
  `{env, thread_id, reply_to, mentions, content_chars}` (not the text).

Response `201`:

```json
{ "data": { "id": "5c0e…", "thread_id": "9d4c…", "author_type": "human", "author": "Mhmd",
  "kind": "text", "content_md": "@Historian what usually happens after CPI?", "card": null,
  "reply_to": null, "mentions": ["historian"], "reasoning_md": null, "tool_calls": null,
  "tokens_in": null, "tokens_out": null, "cost_usd": null,
  "created_at": "2026-10-29T05:00:00.123456Z" } }
```

Errors: `422` `{ "message": "…", "errors": { "content_md": ["…"] } }`, `401`, `429`.

Envelope published on `atlas.warroom` (one entry, field `envelope`), inside the same DB transaction
(if Redis is down the post fails with 500 and nothing is stored):

```json
{
  "id": "<warroom_messages.event_id>",
  "type": "warroom.message",
  "ts": "2026-10-29T05:00:00.130Z",
  "env": "demo",
  "source": "human:owner",
  "correlation_id": null,
  "payload": {
    "thread": { "kind": "main", "key": "main" },
    "thread_id": "9d4c1b7e-2f0a-4c1e-8a55-0f6a3c2e1b10",
    "message_id": "5c0e8f9a-3b1d-4d2e-9f60-7a8b9c0d1e2f",
    "author_type": "human",
    "author": "Mhmd",
    "kind": "text",
    "content_md": "@Historian what usually happens after CPI?",
    "reply_to": null,
    "mentions": ["historian"],
    "created_at": "2026-10-29T05:00:00.123456Z"
  }
}
```

The SPA should treat the 201 body as authoritative and dedupe the Reverb echo by `id`.

## GET /api/warroom/replay?date=YYYY-MM-DD&env=demo

Every message of that **Asia/Baghdad** day for the env, all threads, oldest first
(`created_at asc, id asc`). Baghdad is UTC+3, so `2026-10-28` = `2026-10-27T21:00:00Z` ≤ created_at <
`2026-10-28T21:00:00Z`. `date` required, format `Y-m-d` (else 422). Not paginated.

```json
{
  "data": [ { "id": "…", "thread_id": "…", "author_type": "agent", "author": "historian", "…": "…" } ],
  "meta": { "date": "2026-10-28", "timezone": "Asia/Baghdad",
            "from": "2026-10-27T21:00:00.000000Z", "to": "2026-10-28T21:00:00.000000Z" }
}
```

Group by `thread_id` client-side for the scrubber.

## Not built here

Thread creation over HTTP (instrument/agent threads come from the bridge's `firstOrCreate`), slash
commands (`/pause`, `/kill` …), read/unread state, agent status/cost (Agents page, patch 9), and message editing/deleting.
