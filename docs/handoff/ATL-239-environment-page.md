# ATL-239 — Settings → Environment page (frontend)

For ATL-240 (live everywhere) and anyone touching the env in the SPA.

## The active env in the SPA

`src/app/shell/tradingEnv.ts` is now real:

- `useEnvironment()` — TanStack query on `GET /api/environment`, key `['environment', lang]`
  (`environmentKey(lang)`), sends `Accept-Language` (`langHeaders(lang)`) so pre-flight details come
  back translated. staleTime 30 s, no retry.
- `useTradingEnv()` — `'live'` when `active_env === 'live'`, otherwise `'demo'` (also while loading or
  on error). `EnvBadge`, `RiskFooter` and `JournalPage` already call it, so they follow the switch.
- Types `EnvironmentDoc`, `PreflightItem`, `PreflightKey` live there too.

A component that calls `useTradingEnv()` now needs a `QueryClientProvider` (AppShell.test's footer
test was wrapped for that).

## After a switch

`useSwitchEnv()` (`features/settings-environment/model.ts`): on 200 it `setQueryData`s the env query
with the response, then `invalidateQueries` on **every other query** (not awaited), so the toolbar,
footer and every page refetch with no reload. There is no `system.env_changed` Echo listener yet
(a switch from another tab only shows after the 30 s stale time / refocus); ATL-240 may add one.

## What changed outside the feature

- `tokens.css`: `--live`, `--live-on`, `--live-ink`, `--live-wash` (Live readiness §2; they reuse the
  system orange and the warning ink/wash values) and Tailwind colours `live`, `live-on`, `live-ink`,
  `live-wash`.
- `EnvBadge`: the full badge is solid `bg-live text-live-on` in live. The compact line, tooltip and
  footer live styles are still ATL-240's (RiskFooter still colours "Live" with loss-ink).
- `Sheet`: new tones `live` and `demo` for the icon tile.
- Backend: attested pre-flight items now also carry `confirmed_at` (ISO string or null);
  computed items do not have the key. Pest test in EnvironmentApiTest.

## Page

`/settings/environment`, nav order 190 (last in Settings). Fix links: Connections, and Risk /
Autonomy / Money limits with `?env=live` (ATL-242 reads that). Not built from the design: the
demo-history bar and "Passes on <date>" (the API gives no day count), and the capital-cap figure in
the switch sheet (reading `/api/settings?env=live` would create the live row and flip a check).
