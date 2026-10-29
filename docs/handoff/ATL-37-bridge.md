# ATL-37 — `php artisan atlas:bridge`: streams → Postgres → Reverb

For ATL-38/ATL-39 (War Room API + UI) and ATL-40 (composer and live updates over Echo), and anyone
publishing bus events. Read `docs/handoff/ATL-36-event-bus.md` for the tables, stream layout and
envelope; this file covers what the bridge does with them and what the browser receives.

## The two-minute version

- Listen with a **leading dot**: `Echo.private('warroom').listen('.warroom.message', (m) => …)`.
  Without the dot Echo prefixes `App\Events\` and nothing fires.
- The `.warroom.message` payload is exactly `WarroomMessageResource` JSON — the same object the War
  Room API returns per message. Merge it into the TanStack Query cache by `id`.
- A message is broadcast **once, when it is first stored**. If the bridge dies between the insert and
  the broadcast, the row is there but the live event is not: on Echo (re)connect, refetch the newest
  page from the API. Postgres is the record; the socket is a hint.
- A broadcast failure (Reverb down) is logged and the entry is still acked — same rule.
- `created_at` is the **envelope `ts`** (publish time), not the insert time, so a backlog drained after
  a restart keeps its order.

## Process

```
php artisan atlas:bridge [--consumer=bridge-1] [--block=1000]
```

- Reads all seven `atlas.*` streams with `XREADGROUP GROUP bridge <consumer> COUNT 100 BLOCK <block>`.
- Consumer group `bridge` is created on first run: from `0` on `atlas.warroom` (anything published
  before the first run is still persisted), from `$` on the other six (stale broadcasts are useless).
- On start, and after any transient failure (DB/Redis down — logged as `atlas.bridge.retrying`,
  1 s back-off), it drains the group's pending entries first with `XAUTOCLAIM … 0` (claims every
  delivered-but-unacked entry, from any consumer name), then reads new ones. Only one bridge should
  run; a second one would steal the first's in-flight entries.
- `XACK` after each entry is handled. A transient failure leaves the entry pending (retried on the
  next drain). Entries trimmed while pending are acked.
- SIGTERM / SIGINT: finishes the current entry, returns, exit code 0. Unhandled entries of the
  current batch stay pending and are drained next start.
- Run by `./atlas up` (OPS, e25474a) next to `php artisan reverb:start`. Background-work list for the
  handover: **`reverb:start` (127.0.0.1:8080) and `atlas:bridge` must both be running for the War Room
  to update live.**

Logs (JSON context, never payload contents): `atlas.bridge.started`, `atlas.bridge.stopped`,
`atlas.bridge.malformed {stream, entry_id, reason}` (reason names fields only, e.g.
`invalid fields: payload.cost_usd`), `atlas.bridge.retrying {exception}`,
`atlas.bridge.broadcast_failed {channel, event, exception}`.

## What happens per entry

| Stream | `type` | Action |
| --- | --- | --- |
| `atlas.warroom` | `warroom.message` | validate → upsert into `warroom_messages` by `event_id` → if newly inserted, broadcast `warroom.message` on `private-warroom` |
| `atlas.warroom` | anything else (e.g. `warroom.thinking`) | broadcast on `private-warroom`, event name = `type`, payload = the envelope |
| `atlas.account` | any | broadcast on `private-account`, event name = `type`, payload = the envelope |
| `atlas.system` | any | broadcast on `private-system`, event name = `type`, payload = the envelope |
| `atlas.market`, `atlas.proposals`, `atlas.risk`, `atlas.orders` | any | ack only (no browser channel yet; `private-market.{instId}` comes with patch B) |

Malformed → logged + acked, never retried: not JSON / not an object; no `envelope` field; envelope
missing `id` (uuid), `type` (`a.b` lowercase dotted, ≤ 64), `ts` (ISO-8601), `env` (`demo`|`live`),
`source`, `correlation_id` (present, may be null) or `payload` (object). For `warroom.message` also:
payload fails the rules below, `thread_id` not a thread in that env, `reply_to` not a message in that env,
or `thread` `{kind: main}` with a key other than `main` (or key `main` on a non-main kind).

## `warroom.message` payload (tightened from ATL-36's proposal)

```json
{
  "thread": { "kind": "instrument", "key": "BTC-USDT-SWAP" },
  "author_type": "agent",
  "author": "historian",
  "kind": "research_report",
  "content_md": "**Verdict:** weakly supports",
  "card": { "verdict": "weakly_supports", "n": 14 },
  "reply_to": null,
  "mentions": ["market_scout"],
  "reasoning_md": null,
  "tool_calls": [{ "tool": "history.compute_stats", "input": { "inst_id": "BTC-USDT-SWAP" } }],
  "tokens_in": 1200,
  "tokens_out": 340,
  "cost_usd": "0.0123"
}
```

| key | rule |
| --- | --- |
| `thread` | `{kind: main\|instrument\|agent, key: string ≤ 64}`; required unless `thread_id` (uuid of a thread in the same env) is sent. Missing instrument/agent threads are created with `title` = key; main → `Main` |
| `author_type` | `agent` \| `human` \| `system` |
| `author` | string ≤ 64 |
| `kind` | `^[a-z][a-z0-9_]*$`, ≤ 32 (not limited to `WarroomMessage::KINDS`, on purpose) |
| `content_md` | string, must be present (may be empty) |
| `card` | object or null |
| `reply_to` | uuid of a message in the same env, or null |
| `mentions` | list of strings ≤ 64, default `[]` |
| `reasoning_md` | string or null |
| `tool_calls` | list of objects or null |
| `tokens_in`, `tokens_out` | integer ≥ 0 or null |
| `cost_usd` | **decimal string** `^\d{1,12}(\.\d{1,6})?$` or null — a JSON number is rejected |

## Browser channels and events

Channel auth: `POST /broadcasting/auth` (Laravel's default route, `web` middleware, so the Sanctum SPA
session cookie authenticates it; the request needs the `X-XSRF-TOKEN` header like any other SPA POST).
Guest → **403**; any signed-in user (Atlas has one owner) → 200 for `private-warroom`,
`private-account`, `private-system`. Any other private channel → 403.

| Channel (Echo name) | Event (listen with) | Payload |
| --- | --- | --- |
| `private-warroom` (`Echo.private('warroom')`) | `.warroom.message` | `WarroomMessageResource` (below) |
| `private-warroom` | `.warroom.thinking` etc. | envelope |
| `private-account` (`Echo.private('account')`) | `.<type>` e.g. `.account.balance` | envelope |
| `private-system` (`Echo.private('system')`) | `.<type>` e.g. `.system.settings_changed` | envelope |

`.warroom.message` payload, real example from the manual run below:

```json
{
  "id": "01a184f7-ef76-730c-9478-9f44035dc3ad",
  "thread_id": "01a184f7-ef72-72c0-a93a-1f16205708ab",
  "author_type": "agent",
  "author": "historian",
  "kind": "text",
  "content_md": "manual run ATL-37",
  "card": null,
  "reply_to": null,
  "mentions": ["market_scout"],
  "reasoning_md": null,
  "tool_calls": null,
  "tokens_in": null,
  "tokens_out": null,
  "cost_usd": "0.012300",
  "created_at": "2026-10-29T00:03:23.574009Z"
}
```

Envelope payload for the other events (keys always in this order):

```json
{
  "id": "0b8f3c2e-6a51-4f0e-9a43-1c2d7e5b9f10",
  "type": "system.settings_changed",
  "ts": "2026-10-29T05:00:00.123Z",
  "env": "demo",
  "source": "system:settings",
  "correlation_id": null,
  "payload": { "version": 7 }
}
```

Note the War Room resource has no `env`; the thread id tells you which env a message belongs to
(threads are per env). Filter `.warroom.message` events whose `thread_id` is not in the active env's
thread list. Envelope-shaped events carry `env` directly.

## Other change

`EventBus::publish` (PHP) now refuses an `env` other than `demo`/`live`
(`InvalidArgumentException("Unknown env [prod].")`), matching Python's `EventEnvelope` (QA's ATL-36 note).

## Manual run (Python publisher → bridge → real Reverb), 2026-10-29

Reverb on 127.0.0.1:8091, the bridge with `BROADCAST_CONNECTION=reverb`, a Pusher-protocol websocket
client subscribed to `private-warroom` (signed with the app secret), and the Python publisher sending
the same `EventEnvelope` twice:

```
entry 1793232203584-0
entry (same envelope again) 1793232203590-0
SUBSCRIBED pusher_internal:subscription_succeeded
EVENT private-warroom warroom.message {"id":"01a184f7-…","author":"historian",…,"cost_usd":"0.012300",…}
NO_DUPLICATE          (no second event within 3 s)
rows: 1               (warroom_messages for that thread)
bridge exit 0         (after SIGTERM)
```

## Tests

`backend/tests/Feature/Warroom/BridgeTest.php` (fake broadcaster `Tests\Support\RecordingBroadcaster`):
one row + one broadcast; same envelope twice/three times → one row, one broadcast; read-not-acked then
restart → drained, persisted once; persisted-not-acked then restart → no second row or broadcast;
malformed entries (7 kinds + no `envelope` field) acked, logged without contents, next valid one persists;
on-demand threads; `thread_id` + `reply_to`; account/system/thinking broadcasts; channel auth 403/200.
`BridgeCommandTest.php` runs the real process, publishes, waits for the row, sends SIGTERM, asserts exit 0.

## Not built here

The War Room API routes, `private-market.{instId}`, the Echo client setup (ATL-40), per-env channels.
