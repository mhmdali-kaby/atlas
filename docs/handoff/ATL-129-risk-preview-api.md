# ATL-129 — GET /api/settings/risk-preview

For ATL-130 (Settings → Money limits page).

## Two-minute version

- `GET /api/settings/risk-preview?env=demo` (`routes/api/settings_preview.php`,
  `App\Http\Controllers\Settings\RiskPreviewController`, `App\Support\Settings\RiskPreviewService`).
  Read-only, Sanctum-authed, 200 on every valid request — it never writes a settings row, a
  version or history.
- It reads the latest `equity_curve` row for the env and the current settings (`SettingsService`),
  then applies any query-param drafts **only to this one computation**. It never touches the
  stored settings.
- Decision #250: every percentage limit is computed on `sizing_equity_usd = min(equity_usd,
  bot_capital_cap_usdt)`, or `equity_usd` when the cap is `null`. `cap_binds` is `true` only when
  the cap is set and strictly less than equity (the cap actually changed the result).
- Two figures, two binding-limit fields: `max_risk_usd` has one term
  (`max_loss_per_trade_pct` of the sizing equity), so `risk_binding_limit` is always
  `"max_loss_per_trade_pct"`. `max_notional_usd` is the smallest of three terms, and
  `notional_binding_limit` names whichever won (ties broken in the order below).

## Response shape

```json
{
  "data": {
    "env": "demo",
    "settings_version": 3,
    "equity_usd": "100000.00",
    "equity_as_of": "2026-10-30T08:00:00.000000Z",
    "sizing_equity_usd": "10000.00",
    "cap_binds": true,
    "max_risk_usd": "100.00",
    "risk_binding_limit": "max_loss_per_trade_pct",
    "max_notional_usd": "500.00",
    "notional_binding_limit": "max_position_pct",
    "used_exposure_today_usd": "0.00",
    "trades_today": 0,
    "reason": null
  }
}
```

With no `equity_curve` row for the env yet, every money/limit field is `null`, `cap_binds` is
`false`, `trades_today` is `0`, `used_exposure_today_usd` is still `"0.00"` (it does not depend on
equity), and `reason` is `"no_equity"`. The response is still `200`.

Every money field is a decimal string with **exactly** two decimal places (`BigDecimal::toScale(2,
HalfUp)`), never a float, including the no-equity case's `"0.00"`. `trades_today` is a plain JSON
integer. `cap_binds` and `reason: null`/`"no_equity"` are the two states to branch the UI on.

`notional_binding_limit` is one of `max_position_pct`, `max_usdt_per_trade`,
`max_new_exposure_per_day` — note the last one drops `_usdt` to match the Risk Engine's own cap
name (ATL-126's `binding_cap`), not the settings field `max_new_exposure_per_day_usdt`.

## Draft query params (preview an unsaved edit, no version written)

Any of these, as plain query params, override the saved value for this one request only:
`risk_profile` (one of `conservative|balanced|aggressive`, applies that preset's
`max_loss_per_trade_pct` and `max_position_pct` before any other draft param), `max_loss_per_trade_pct`,
`max_position_pct`, `max_usdt_per_trade`, `max_new_exposure_per_day_usdt` — all decimal strings,
same ranges as the settings schema — and `bot_capital_cap_usdt`, which is the one nullable field:
send `bot_capital_cap_usdt=` (empty) or `bot_capital_cap_usdt=null` to preview "no cap", any other
value as a decimal string to preview a cap.

```
GET /api/settings/risk-preview?env=demo&max_position_pct=2
GET /api/settings/risk-preview?env=demo&bot_capital_cap_usdt=
```

A draft outside its field's range, or not a decimal string, is `422` with the error on that bare
field name (`max_position_pct`, not `changes.max_position_pct` — this endpoint has no `changes`
wrapper, unlike `PATCH /api/settings`).

## used_exposure_today_usd / trades_today

Sum of `risk_checks.final_notional_usd` and a count, for `orders` rows in this env with
`origin = 'atlas'`, `purpose = 'entry'`, `created_at` on or after the start of today in
**Asia/Baghdad** (not UTC midnight), joined to `orders.risk_check_id`. Both are `0`/`"0.00"` until
the Executor (patch 23) exists — there is nothing to seed for that in Phase 2's earlier patches.
This is also what shrinks the `max_new_exposure_per_day` term:
`remaining = max(0, max_new_exposure_per_day_usdt − used_exposure_today_usd)`.

## Not built here

Any UI. No Eloquent models were added for `orders`/`risk_checks`/`proposals` — this reads them
with `DB::table()`, same as `PortfolioService`.
