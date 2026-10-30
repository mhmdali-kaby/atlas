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

## The blocklist (read this before building a blocklist editor)

Settings → Markets (brief §14.5) has no blocklist field yet — no task builds it in Phase 1E. Until
one does, the blocklist is `config('atlas_defaults.markets.blocklist')`, a plain list of `inst_id`
strings, added to `config/defaults.yaml` by this task (empty by default). It is **not** part of the
versioned `settings` table, so changing it today means editing the YAML and running
`php artisan atlas:defaults`, not a Settings PATCH. When a real Settings → Markets blocklist field
is built, point this controller's `blocklist()` helper at it and delete the YAML key — don't leave
two sources of truth.

## What this does not do

No audit_log entry (it's a read). No pagination beyond `limit` — there's no cursor, because a
picker only ever wants the first page. `q` is escaped for Postgres' `LIKE` special characters
(`%`, `_`, `\`) but not otherwise sanitised beyond length; it is never interpolated into raw SQL.

## Decision worth flagging

I added `markets.blocklist: []` to `config/defaults.yaml` (and regenerated the PHP/Python
constants) purely as a read source for this endpoint, since nothing in the codebase defined a
blocklist anywhere yet and ATL-119's acceptance criteria require one to exist. This is a stand-in,
not a decision about where the real one should live — flag it to @TL if a Settings → Markets task
gets scheduled, so it replaces this instead of living alongside it.
