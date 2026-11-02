# ATL-215 — Telegram: API, sending, buttons, poller

For the Settings → Notifications screen and anyone touching approvals. Brief §14.9, §17, decision #254 (3).

## The two-minute version

- One bot per install (`telegram_connections`, at most one row). Token and chat id are `encrypted`
  casts; the API only ever returns `bot_token_last4` / `chat_id_last4`.
- Which events go to Telegram is still the ATL-214 settings keys `notifications.<event>.telegram`
  (default off). Saving a bot does **not** turn any of them on — the screen must say so / offer it.
- A new notification with `channels.telegram = true` → `App\Listeners\QueueTelegramNotification`
  (auto-discovered listener on `NotificationCreated`) → queued `App\Jobs\SendTelegramNotification`
  (Horizon `default` queue, 3 tries) → `sendMessage`. Plain text, no `parse_mode`.
- Approve/Reject from the web and from Telegram now both go through
  `App\Support\Proposals\ProposalApprovalService` (`approve($p, 'owner'|'telegram', ?User)`,
  `reject($p, actor, reason, ?User)`). The trading_core call is byte-identical
  (`POST /internal/proposals/{id}/execute` with `{"approved_by":"owner"}`) because trading_core
  only accepts `owner`; the Telegram origin is recorded in Laravel: `audit_log.after.actor =
  "telegram"` and the `atlas.proposals` envelope has `source: "human:telegram"`, `via: "telegram"`
  (web: `human:owner`, `via: "owner"`). `decided_by` stays `owner` — it is the owner's decision.
- `php artisan atlas:telegram-poll` (now in `./atlas up`'s process list) long-polls getUpdates.
  `--once` runs one round. With no bot configured it sleeps and re-checks.

## API (auth:sanctum, throttle `telegram-write` = `telegram.writes_per_minute` 30/min on writes)

`GET /api/notifications/telegram` → 200

```json
{"data": {"configured": true, "bot_token_last4": "WXYZ", "chat_id_last4": "4321",
  "last_sent_at": "2026-11-02T09:15:01.120000Z", "last_tested_at": null, "last_error": null,
  "updated_at": "2026-11-02T09:00:00.000000Z"}}
```

Not configured: every field null and `"configured": false`.

`PUT /api/notifications/telegram` body `{"bot_token": "123456789:AAH…", "chat_id": "987654321"}` →
201 (created) / 200 (replaced), same `data` shape. Validation 422: `bot_token` must match
`^\d{3,20}:[A-Za-z0-9_-]{20,100}$`, `chat_id` `^\d{1,20}$` (string) — a private chat only, no
leading minus (ATL-221: a negative id is a group/channel, and group members are not the owner).
Both required every time. A different token resets the stored update offset.

`DELETE /api/notifications/telegram` → 200 `{"data": {"configured": false, …}}` (idempotent).

`POST /api/notifications/telegram/test` → sends `telegram.test_message` in the owner's language.

```json
{"ok": true, "error": null, "message": null, "data": {…}}
```

Failure 502 `{"ok": false, "error": "test_failed", "message": "Telegram did not accept…", "data": {… "last_error": "Telegram sendMessage failed (401): Unauthorized"}}`.
No bot: 409 `{"ok": false, "error": "not_configured", "message": "…"}`.

`last_error` is safe to show: the client scrubs the token out of every message (`[redacted]`).
Audit actions: `telegram.created|updated|deleted|tested` (last 4 only), `telegram.callback_refused`.

## Message text

`lang/{en,ar}/telegram.php` → `telegram.events.<event>` with the ATL-214 params; language is the
first user's `users.locale`. Booleans → yes/no, lists joined, `approval_expires_at`/`since`/
`resets_at` in Asia/Baghdad. Each message starts `[DEMO]`/`[LIVE]` and ends with
`Open: <FRONTEND_URL><link>`.

## Buttons — callback_data

Only on `proposal_awaiting_approval`, and only while the proposal is `awaiting_owner` and before
`approval_expires_at`:

`<a|r>:<proposal_id>:<expires_unix>:<hmac>` — **shortened** to fit 64 bytes (60 used):

- `proposal_id` = the UUID's 16 bytes, unpadded base64url (22 chars);
- `expires_unix` = `approval_expires_at` unix seconds;
- `hmac` = first 18 bytes (`telegram.callback_mac_bytes`) of HMAC-SHA256 over
  `"<a|r>:<id as sent>:<expires>"`, unpadded base64url (24 chars). Key = HMAC-SHA256(APP_KEY bytes,
  `"atlas.telegram.callback.v1"`).

`App\Support\Telegram\CallbackData::encode()/decode()`. Rotating APP_KEY kills every sent button.

## Poller rules

- Offset: `telegram_connections.update_offset` = last update_id taken, written under a row lock
  **before** handling; anything ≤ it is skipped. At-most-once (a crash mid-handle drops that press,
  the owner presses again or uses the web).
- Ownership (ATL-221): the press is refused unless `message.chat.id` is the stored chat id,
  `message.chat.type` is `private`, **and** `callback_query.from.id` equals that same stored chat
  id (`hash_equals`, not just `==`). A press from anyone else — a different `from.id`, or any
  non-`private` chat (group, supergroup, channel) — is answered `telegram.callback.expired`
  ("expired") and audited as `telegram.callback_refused` with reason `foreign_user`; nothing about
  the proposal changes. Offset still advances.
- Bad/tampered data, unknown proposal, `now ≥ expires_unix`, or the service says
  `approval_expired` / `not_awaiting_owner` / `not_rejectable`: answer `telegram.callback.expired`
  ("expired"), audit `telegram.callback_refused`, nothing else changes.
- Valid: service call, `answerCallbackQuery` with the result, `editMessageText` = original text +
  outcome line, buttons removed. Other approve errors (e.g. `settings_changed`) are shown with
  the `proposals.*` message.

## Not built

The Settings → Notifications UI (token form, test button, per-event telegram toggles); i18n for
the SPA side. Reject from Telegram stores the fixed reason `telegram.reject_reason` — there is no
way to type one.
