# ATL-214 — Notifications: events, payload, channel, routes

For the SPA notifications task and ATL-215 (Telegram). Brief §17, decision #254 (2).

## The two-minute version

`php artisan atlas:bridge` now offers every bus entry to `App\Support\Notifications\Notifier`.
The 11 brief §17 events become one row in `notifications` per **envelope id** (`source_event_id`,
unique — a replayed envelope stores nothing and broadcasts nothing). A new row is broadcast as
`notification.created` on `private-system` (only when `in_app` or `browser` is on) and
`App\Events\NotificationCreated` is dispatched (always, once). Titles are **i18n keys + params**,
never English: the SPA must add `notifications.events.<event>.title` (and any body text) to
`src/i18n/locales/<lang>/notifications.json` in en and ar.

## Events

| event | severity | from (stream / type) | params | link |
|---|---|---|---|---|
| `proposal_awaiting_approval` | warning | atlas.proposals `proposal.awaiting_owner` | proposal_id, inst_id, side, kind, final_notional_usd, approval_expires_at | `/warroom?proposal=<proposal_id>` |
| `order_filled` | info | atlas.orders `order.state_changed` → `filled`, origin `atlas` only | order_id, proposal_id, inst_id, side, purpose, filled_sz, avg_px | `/portfolio/orders?inst_id=<inst>` |
| `stop_hit` | warning | atlas.orders `algo_order.state_changed` → `effective`, kind `sl` (or `oco` with actual_side `sl`) | algo_order_id, inst_id, side, trigger_px, actual_px, sz | `/portfolio/positions?inst_id=<inst>` |
| `target_hit` | info | same, kind `tp` (or `oco` with actual_side `tp`) | same | same |
| `circuit_breaker` | critical | atlas.system `system.circuit_breaker`, state `tripped`, any name except the two below | name, reason, since, self_clearing | `/warroom` |
| `data_stale` | warning | same, name `data_stale` | same | `/settings/data` |
| `reconciliation_mismatch` | critical | same, name `reconciliation_mismatch` | same | `/portfolio/positions` |
| `kill_switch` | critical | atlas.system `system.kill` | flatten, ok, cancelled, closed, stops_kept, failed (count) | `/warroom` |
| `agent_degraded` | warning | atlas.system `system.agent_state` into `degraded`/`rate_limited` from any other state | agent, state, previous_state, resets_at | `/agents` |
| `daily_report` | info | atlas.warroom `warroom.message` by agent `portfolio_manager` with `card.type = daily_report` | message_id, day (Baghdad Y-m-d) | `/warroom?message=<message_id>` |
| `news_alert` | warning (critical at importance 5) | atlas.warroom `news.alert` with reason `held_instrument` (or non-empty held_inst_ids) | message_id, headline (plain text, untrusted), source_name, importance, inst_ids (held) | `/warroom?message=<message_id>` |

Not notified: breaker clears, orders that are not Atlas's own (an OKX stop firing shows up as an
external order — it is told as stop_hit/target_hit instead), agents staying degraded, news alerts on
instruments not held, other proposal status changes.

Params are an allow-list per event: strings are plain text cut to 300 chars, decimals stay strings,
nothing else from the payload is copied (so no key/secret/UID/error text can reach a row). The
`?proposal=`, `?message=` and `?inst_id=` query params are new — the War Room and Portfolio pages do
not read them yet; the SPA task decides how to honour them (scroll/highlight/filter).

To tell an OCO's legs apart, trading_core's algo payload now also carries `actual_side`
(`sl`|`tp`, OKX `actualSide`) and `actual_px`; without `actual_side` the nearest trigger to
`actual_px` decides, and with neither no notification is made.

## Broadcast — `notification.created` on `private-system`

Payload = the API row below. Echo: `echo.private('system').listen('.notification.created', cb)`.

```json
{
  "id": "01a1a2b3-0000-7000-8000-000000000001",
  "env": "demo",
  "event": "order_filled",
  "severity": "info",
  "title_key": "notifications.events.order_filled.title",
  "params": {"order_id": "6a2d4c10-1f7e-4c55-8d61-0e9b2a3f4c22", "proposal_id": "0b8f1c52-7d0e-4a8e-9a39-3b4c1e0f6a11", "inst_id": "XAU-USDT-SWAP", "side": "buy", "purpose": "entry", "filled_sz": "3", "avg_px": "2650.1"},
  "link": "/portfolio/orders?inst_id=XAU-USDT-SWAP",
  "channels": {"in_app": true, "browser": true, "telegram": false},
  "source_event_id": "5f0e7a7c-3d1b-4b8a-9f2e-6c1d2e3f4a5b",
  "read_at": null,
  "created_at": "2026-11-02T09:15:00.250000Z"
}
```

`created_at` is the envelope's `ts` (when it happened), UTC. Show it in Asia/Baghdad.

## Settings

`notifications.<event>.in_app|browser|telegram` — 33 booleans in GET/PATCH `/api/settings`
(dotted keys like the agent settings). Defaults: in_app and browser on, telegram off
(`config/defaults.yaml` → `notifications.channel_defaults`). Rules:

- every channel off → nothing stored, nothing broadcast, no event;
- in_app and browser off, telegram on → row stored with those `channels`, **not** broadcast,
  `NotificationCreated` dispatched (Telegram sends it);
- the browser decides toast vs. system Notification from `channels.in_app` / `channels.browser`
  (switch values captured when the row was stored).

## API (auth:sanctum, active env only)

- `GET /api/notifications?cursor=&limit=&unread=1` — newest first (`created_at`, `id`), limit
  1–200 (default 50); `unread` accepts `1|0|true|false`; crafted cursor → 422.
  `{"data": [row, …], "meta": {"env": "demo", "per_page": 50, "unread_count": 3, "next_cursor": "eyJ…"|null, "prev_cursor": null}}`
- `POST /api/notifications/{id}/read` — `{"data": row}` with read_at set (idempotent; keeps the
  first read time). 404 for an unknown id or another env's row. Audited `notification.read`
  (before/after read_at).
- `POST /api/notifications/read-all` — `{"data": {"env": "demo", "marked": 2, "unread_count": 0}}`.
  Audited `notification.read_all` (after: env, marked, read_at).

Both POSTs share the `notifications-write` limiter (120/min).

## For ATL-215 (Telegram)

```php
Event::listen(\App\Events\NotificationCreated::class, function ($e) {
    $n = $e->notification;              // App\Models\Notification
    if (($n->channels['telegram'] ?? false) !== true) return;
    // queue the send; for proposal_awaiting_approval, params.proposal_id + approval_expires_at
});
```

It fires inside the bridge process, once per stored row, never for a replay — queue the send
(Horizon) rather than calling Telegram inline, so a slow API never stalls the stream.

## Gotcha

The table is named `notifications`, which is also the name Laravel's database notification channel
uses. Atlas does not use Laravel notifications; do not call `$user->notifications()` (Notifiable's
relation) — use `App\Models\Notification`.
