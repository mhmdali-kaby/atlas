# ATL-18 — Onboarding frame, Welcome and Connect OKX demo

Built from design v2 `js/pages/onboarding.js`, `js/forms.js`, `css/auth.css`, `css/forms.css`.
Everything lives in `frontend/src/features/onboarding/`; import shared pieces from
`src/features/onboarding` (the `index.ts` barrel). Strings: `src/i18n/locales/{en,ar}/onboarding.json`.

## The two-minute version

- **`/onboarding` is outside the shell** (own top bar, stepper, bottom Back/Continue, `RiskFooter`),
  registered as a `publicRoutes` entry wrapped in `RequireAuth` — so it still needs a session.
- **The step is in the URL**: `/onboarding` (Welcome), `?step=connect`, `?step=risk`, `?step=autonomy`.
  Use `onboardingPath(step)` from `paths.ts`. Reload and browser Back keep the step. `?step=risk|autonomy`
  without a verified demo connection redirects to `?step=connect`.
- **The "new owner → onboarding" guard is this feature's index route at `/`** (`OnboardingGate`): sign-in
  lands on `/`; no verified demo connection → `/onboarding`; otherwise the shell's `HomeRedirect` (first
  sidebar page). No edit to `router.tsx` or `AppShell.tsx`. A deep link such as `/portfolio` is **not**
  intercepted (per TL: keep the guard inside the feature). If a GET /api/connections fails, the gate lets the
  owner through rather than trapping them.
- **Steps 3 and 4 (ATL-19)** currently render only their heading and lead (`PendingStep` in `steps.tsx`);
  Continue is enabled there and *Finish setup* navigates to `AFTER_ONBOARDING_PATH` (`/portfolio`).
  ATL-19: replace `PendingStep` in `OnboardingPage.tsx` with the real steps; add the "onboarding
  complete → /onboarding redirects to /portfolio" rule in `OnboardingPage` (and the settings-version
  condition in `OnboardingGate` if you want `/` to go straight to Portfolio only once complete).
- **Continue on Connect** is enabled only when the stored demo connection has `last_verified_at` and the
  owner is not in the middle of *Replace keys*.

## Reusable pieces (for ATL-20 Settings → Connections)

| Export | What it is |
| --- | --- |
| `ConnectionForm` | `{ connection: Connection \| null, env?: 'demo', onResult?(TestOutcome), onTestStart?(), hint?, idPrefix? }`. Label (default "OKX demo"), API key (mono, LTR), Secret key and Passphrase (`SecureField`, masked). Submit = **save then test**: POST `/api/connections` when `connection` is null, else PATCH `/api/connections/{id}` with all four fields, then POST `/api/connections/{id}/test`. All three secrets required client-side. Typed secrets are cleared after a passing test. A 422 maps to the field ("OKX keys don't look like this…"). |
| `useSaveAndTest(env)` | The mutation behind it; writes the returned connection into the `['connections']` cache after save and after test. Resolves a `TestOutcome`; throws `ConnectionValidationError` only for 422 field errors. |
| `TestOutcome` | `{ status: 'ok', connection, acctLv, warnings }` or `{ status: 'failed', error: slug, okxCode, connection? }`. 503/502/network become `trading_core_unavailable` / `trading_core_error`. |
| `TestSuccess` | `{ connection, acctLv?, warnings?, titleRef? }` — seal (seed `okx:<env>:<uid_last4>`) + check, "Connected to OKX demo", `AccountRows`, Spot-mode notice, withdraw notice. |
| `TestFailure` | `{ error, okxCode?, ref? }` — red `Notice` with `role="alert"`; every slug from ATL-8 has an en/ar title + body under `tr.errors.<slug>`, unknown slugs fall back to `tr.errors.unknown`. |
| `AccountRows` | `{ connection, accountMode? }` — account mode (Spot in warning ink), position mode, permission badges (Read, Trade, then "Withdraw off" in green or "Withdraw" in orange), UID `•••• 6123`. No "Last verified" row: add it in Settings (design `accountRows(conn, {verified:true})`). |
| `SavedKey` | `{ connection, onReplace? }` — "Key saved · API key •••• ab12 …" + Replace keys. |
| `WithdrawWarning` | The persistent orange notice (decision 240). |
| `KeyHelp`, `Notice` | Info notice "Create a demo key with Read and Trade only"; the design §9 Notice (`tone: info|success|warning|error`). |
| `useConnections()`, `useConnection(env)`, `isVerified(c)`, `canWithdraw(c, warnings)`, `CONNECTIONS_KEY` | Query helpers over GET `/api/connections` (key `['connections']`). |

## The withdraw notice (decision 240)

- Demo key with Withdraw: test returns `ok:true, warnings:["withdraw_permission_enabled"]` → success card,
  Continue enabled, plus the orange `WithdrawWarning` **inside** the result card.
- It is derived from `connection.permissions` containing `"withdraw"` (stored by trading_core), **or** the
  test's `warnings` — so it survives a reload and never times out. It is not a toast.
- A test that fails with `error: "withdraw_permission_enabled"` (live keys) shows the blocking red
  notice "This key can withdraw funds … create a new key with Read and Trade only", Continue stays disabled.

## Not built here

- Steps 3–4 bodies (ATL-19); the sidebar account chip (still open from ATL-17).
- No "Last verified" time (no Baghdad date formatter exists yet in `src/lib/format.ts`).
- Design gap filled without a design state: error copy for `ip_not_allowed`, `clock_out_of_sync`,
  `key_frozen`, `key_not_found`, `decrypt_failed`, `okx_unreachable`, `okx_error`, `trading_core_error`
  follows ATL-8's messages, in the design's red notice. The demo withdraw warning uses the design's
  `notice-warning` pattern with the PM's copy.

## Tests

- `src/features/onboarding/onboarding.test.tsx` (12, jsdom, fake API through the real router): `/` →
  Welcome with 4 steps, Continue → Connect and heading focus; verified owner not sent to onboarding;
  save + test shows modes/permissions/UID tail and no response body contains key/secret/passphrase;
  wrong passphrase (en + ar); blocking withdraw error; demo withdraw warning after 10 s and after a
  "reload" (fresh cache); Back/Continue shows only `•••• ab12`; required fields; service down;
  skip-ahead URL; stepper back.
- `e2e/onboarding.spec.ts` (6, Chromium): en/ar × light/dark screenshots of both steps and the result,
  stepper order flips in RTL, wrong key, the withdraw notice after 10.5 s and after a real reload.
  Screenshots in `evidence/ATL-18/`.
