# ATL-25 — Recorded flows (Playwright on the real stack)

This is the "Recorded flows" section written into CLAUDE.md. CLAUDE.md is excluded from git in this repository, so the text is kept here too.

Brief §19 browser flows (ATL-25) are Playwright specs in `frontend/e2e/`. Run them all with:

```
cd frontend && npm run e2e
```

Postgres (127.0.0.1:5433) and Redis (127.0.0.1:6380) must be running, with `composer install`,
`npm ci` and `uv sync` done. `npm run e2e` starts everything else itself and stops it at the end
(`frontend/playwright.config.ts`, values in `frontend/e2e/support/stack.ts`):

- the mocked specs (`e2e/*.spec.ts`, project `chromium`) fake Laravel with `page.route` on Vite :3140;
- the recorded flows (`e2e/flows/`) run on a real stack: Vite :3141 → Laravel (`php -S`) :8140 →
  trading_core :8141 with `ATLAS_OKX_FIXTURES=1` (recorded OKX JSON from
  `services/trading_core/tests/fixtures/okx`, nothing reaches OKX) and the scheduler off;
- the `setup` project creates the database `atlas_e2e_<8 hex of sha1(repo root)>` if missing, runs
  `migrate:fresh` + `E2eSeeder` (one un-onboarded owner + `PortfolioDemoSeeder`; it refuses
  production and any database not named `atlas_e2e_*`) and signs in once into storageState;
- then `onboarding` → (`settings`, `ltr-light`, `ltr-dark`, `rtl-light`, `rtl-dark`).

Override ports with `E2E_PORT`, `E2E_FLOW_PORT`, `E2E_API_PORT`, `E2E_CORE_PORT`, the database with
`DB_E2E_DATABASE`, the Redis DB (default 13) with `E2E_REDIS_DB`. One project only:
`npx playwright test --project=rtl-dark` (its dependencies run first). Screenshots go to
`../evidence/ATL-25/` beside the repository.

## Things worth knowing

- **Adding a flow spec**: put it in `frontend/e2e/flows/`, and add a project in `playwright.config.ts`
  that depends on `onboarding` (the owner is onboarded only after that project). Use
  `storageState: STORAGE_STATE` and `baseURL: SPA_URL`. Appearance-sensitive screens go in a
  `*.appearance.spec.ts` and import `test` from `./appearance` (options `lang`, `theme`).
- **Talking to Laravel from a spec**: `page.request.get(`${API_URL}/api/...`)` with headers
  `Origin: SPA_URL`, `Referer: SPA_URL/`, `Accept: application/json` so Sanctum treats it as the SPA.
- **Fixture mode** (`services/trading_core/src/trading_core/okx/fixtures.py`): `/api/v5/<a>/<b>` is
  served from `<a>_<b>.json` (dashes → underscores), `<a>_<b>_<insttype>.json` when `instType` is
  set; a paging cursor (`after`/`before`) gets an empty page; unrecorded endpoints return an empty
  `data` list; `public/time` returns the current time. Non-GET is refused. The recorded
  `account_config` has a zeroed UID and `perm` with withdraw, so a demo Test connection succeeds with
  the `withdraw_permission_enabled` warning and UID tail `0000`.
- **RTL margin check**: an element is an offender when, under RTL, its left margin is non-zero, its
  right margin is zero, **and** the left margin stays when that element's own `direction` is flipped
  to `ltr`. Without the flip, a correct `me-*` (inline-end resolves to the left in RTL) is a false
  positive — the Overview's reconciled-seal `<figure class="m-0 me-3 ...">` is one. Verified by
  temporarily adding `ml-4` to the Overview ledger: the rtl projects fail with
  `<section class="pf-ledger mt-7 ml-4"> margin-left 16px`.
- **php -S, not artisan serve**: `artisan serve` drops the environment it was given; Laravel's
  router script serves from the working directory, so the server runs with `cwd` = `backend/public`.
- `TestDatabase::createIfMissing` is now public so `backend/tests/E2e/database.php` can reuse it.
