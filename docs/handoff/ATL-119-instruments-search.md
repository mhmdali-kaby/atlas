# ATL-119 — Instrument search (`GET /api/instruments`)

Route: `backend/routes/api/instruments.php`. Controller `App\Http\Controllers\Instruments\InstrumentsController`.
Form Request `App\Http\Requests\Instruments\SearchInstrumentsRequest`. Resource `App\Http\Resources\InstrumentResource`.
Tests: `backend/tests/Feature/Instruments/InstrumentsApiTest.php`.

Built for ATL-115's watchlist pin search: find a live instrument of the active env by typing a
few letters of its ticker or inst_id.

## Request

`GET /api/instruments?q=<1-32 chars>&asset_class=&limit=&env=`, Sanctum session required (guest → 401).

- `q` — **required**, 1-32 characters. Matched case-insensitively against the *prefix* of
  `inst_id` or `base_ccy` (e.g. `q=sol` matches `SOL-USDT-SWAP` and `SOL-USDT`, because `base_ccy`
  is `SOL`; it also matches anything whose `inst_id` happens to start with `sol`). Missing, empty
  or 33+ chars → 422 `{errors: {q: [...]}}`.
- `asset_class` — optional, one of `crypto|stock|etf|index|commodity|other` (same enum as
  `App\Support\News\NewsSearch::ASSET_CLASSES`, reused rather than redefined). Invalid value → 422.
- `limit` — optional integer, default **20**, max **50**. Above 50 → 422.
- `env` — optional, `demo|live`, defaults to `atlas_defaults.env.default` — same convention as
  `/api/data/*`, `/api/portfolio/*` and `/api/warroom/*`.

Only `state = 'live'` rows of that one env are ever considered; `suspend`/`delisted_local` rows and
the other env's rows never appear, whatever `q` is.

## Response

```json
{
  "data": [
    {
      "inst_id": "SOL-USDT-SWAP",
      "inst_type": "SWAP",
      "asset_class": "crypto",
      "base_ccy": "SOL",
      "quote_ccy": "USDT",
      "settle_ccy": "USDT",
      "blocked": false
    }
  ]
}
```

Ordering: an **exact** `base_ccy` match (case-insensitive equality with `q`) ranks before a mere
prefix match; within each of those two groups, rows are alphabetical by `inst_id`. The exact-match
group always comes first as a whole, regardless of how its `inst_id`s compare alphabetically to
the prefix-only group's. E.g. with `q=sol`, an instrument `inst_id=SOL` (`base_ccy=XYZ`, an
inst_id-only match) sorts *after* both `SOL-USDT` and `SOL-USDT-SWAP` (`base_ccy=SOL`, exact
matches), even though `"SOL"` alone would otherwise sort first alphabetically.

`blocked: true` means the instrument is on the Settings blocklist (**ATL-115: disable it in the
picker, don't hide it** — the owner should see why it's greyed out).

## The blocklist — one source, same as Market Scout (decision #247)

There is already a blocklist, and this endpoint reads it rather than keeping its own: the env's
`settings` row's `data['blocklist']` when that key is set, else `config('atlas_defaults.agents.scout.universe.blocklist')`
(`config/defaults.yaml` → `agents.scout.universe.blocklist`, empty by default). This is exactly the
rule Market Scout's `config_from` applies (`services/agents/src/atlas_agents/scout/universe.py:151-166`),
so an instrument the Scout drops as blocked is the same one this endpoint marks `blocked: true` —
there is no second list to keep in sync. Matching is case-insensitive on both sides (`inst_id` and
every blocklist entry are upper-cased before comparing).

The controller reads the `settings` row directly (`Setting::query()->where('env', $env)->first()`),
**not** `SettingsService::current()` — that method creates the row and writes a settings_history
entry on first read, which a GET must never do.

Until Settings → Markets (brief §14.5) has an editable blocklist field, changing the default list
means editing `config/defaults.yaml` and running `php artisan atlas:defaults`. Changing it per-env
today means writing `data.blocklist` on that env's `settings` row (e.g. through a future PATCH
`/api/settings` field) — ATL-115's picker does not need to know which source is live, since the
`blocked` flag already reflects it.

## What this does not do

No audit_log entry (it's a read). No pagination beyond `limit` — there's no cursor, because a
picker only ever wants the first page. `q` is escaped for Postgres' `LIKE` special characters
(`%`, `_`, `\`) but not otherwise sanitised beyond length; it is never interpolated into raw SQL.
