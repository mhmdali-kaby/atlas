# ATL-238 — first live month limits and the live Approve confirmation (backend + Risk Engine)

For ATL-240 (live Approve sheet, badges) and ATL-242 (editing the live settings row).

## The first live month

`first_month_days` (30) from `system_state.live_started_at` (ATL-236). Before the first switch to live
(`live_started_at` null) there is no month and nothing below applies. Demo is never affected.
PHP: `App\Support\System\FirstLiveMonth::{endsAt(), active(), maxAutonomy()}`.
Python: `trading_core.risk.rules.in_first_live_month(env, live_started_at, now)`,
`first_live_month_max_autonomy()`, `trading_core.risk.context.load_live_started_at(session)`.

## PATCH /api/settings with `env: "live"` inside the month

- `changes.autonomy_level` > 2 → 422:
  `{"errors": {"changes.autonomy_level": ["During the first live month (until 2026-12-02 10:12) live autonomy can be at most level 2."]}}`
  (Arabic with `Accept-Language: ar`; `until` is in Asia/Baghdad, `Y-m-d H:i`).
- `changes.bot_capital_cap_usdt: null` (clearing) → 422:
  `{"errors": {"changes.bot_capital_cap_usdt": ["During the first live month (until …) the live bot capital cap must stay set."]}}`.
  Changing the cap to another amount is accepted.
- After day 30 both are accepted again.

## SettingsResource (GET/PATCH /api/settings)

Two new keys in `data`:

```json
{"first_live_month_ends_at": "2026-12-02T07:12:00.000000Z", "max_live_autonomy_level": 2}
```

`first_live_month_ends_at` is set only for the **live** row **while the month runs**; null for demo,
before any switch, and after the month. Use it to disable autonomy 3–4 and the "clear cap" control on
the live row, with the date in the hint.

## Risk Engine (live proposals inside the month)

- Check 1 `env_and_kill_switch` rejects with code `live_capital_cap_required` when the live row has no
  `bot_capital_cap_usdt` (values gain `capital_cap_required: true`, `capital_cap_set`).
- Check 9 `autonomy`: level 3 or 4 → `awaiting_owner`, values `note: "first_live_month"`,
  `first_live_month_max: 2`. Position actions (PM amend/close) get the same check-9 cap, but **no**
  cap check: a close is never blocked by a missing cap.

## POST /api/proposals/{id}/approve

For a proposal with `env: "live"` the body must be `{"live_confirmed": true}` (JSON boolean true only;
`"true"`, `1`, false or absent are refused). Otherwise, with nothing sent to trading_core:

```json
HTTP 422
{"ok": false, "error": "live_confirmation_required",
 "message": "This is a live proposal with real money. Confirm the live approval to send it.",
 "execution": [], "proposal": { …summary… }}
```

Demo proposals ignore the field. **The SPA's Approve currently sends no body, so every live Approve
will get this 422 until ATL-240's "real money" sheet sends `live_confirmed: true`.**

## Telegram

Approve on a live proposal is refused before the approval service: the proposal stays
`awaiting_owner`, the callback is answered "Live trade: approve it in Atlas. Reject still works here."
(Arabic for an Arabic owner), the message is not edited, and an audit row
`telegram.callback_refused` is written with subject the proposal and after
`{"actor": "telegram", "reason": "live_approve_in_atlas"}`. Reject works as for demo.

## Not changed

The kill switch (#251). The pre-flight items (ATL-236) are untouched.
