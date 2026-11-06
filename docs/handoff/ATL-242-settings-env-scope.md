# ATL-242 — "Settings for: Demo | Live" on Risk, Money limits, Autonomy and Markets

For ATL-240 (live badges, live Approve sheet, the live-only autonomy/money copy) and ATL-239 (Environment's fix links).

## What the owner gets

Each of the four panes has a segmented control "Settings for [● Demo | ● Live]" in the header (design v21
`js/live.js` `scopeControl`). It picks which settings row the pane reads and saves. While the selected row is
not the env Atlas trades on, a note sits under the header:

- Live selected, Atlas on demo: `settings:scope.editLive` (orange live wash, radio-tower icon).
- Demo selected, Atlas on live: `settings:scope.editDemo` (teal demo wash, flask icon).

No backend change: GET `/api/settings?env=<env>`, PATCH `/api/settings` with `{ env, version, changes }`,
`/api/settings/history?env=`, `/api/settings/risk-preview?env=` and `/api/settings/markets/eligibility?env=` all
already took an env.

## The URL is the state

- `?env=live` / `?env=demo` selects the row. Environment's fix links should use `/settings/risk?env=live`,
  `/settings/money?env=live`, `/settings/autonomy?env=live`, `/settings/markets?env=live`.
- No param = the active env.
- Clicking a segment writes `?env=<choice>` with `replace: true` (no history entry).
- Each page is `XPage() → <XPane key={scope.env} scope={scope} />`, so switching rows drops any unsaved draft
  and starts from that row's saved values.
- Cross-links between the panes keep the env when it is not the active one (Risk → Autonomy, Autonomy → Money,
  Money → Risk).

## Code

`frontend/src/features/settings-risk/EnvScope.tsx`:

```ts
useEnvScope(): { env: 'demo' | 'live'; active: 'demo' | 'live'; setEnv(env): void }
<EnvScopeControl scope={scope} />   // data-testid="env-scope", radiogroup named "Settings for"
<EnvScopeNotice scope={scope} />    // data-testid="env-scope-notice", renders null when env === active
```

**`active` comes from `useTradingEnv()` (`src/app/shell/tradingEnv.ts`), which still returns the constant
`'demo'`.** When ATL-240 makes that hook read `GET /api/environment` → `active_env`, these panes follow with no
other change. That is the one thing to know.

Live tokens `--live`, `--live-on`, `--live-ink`, `--live-wash` (light/dark, from design v21 tokens.css) are now in
`src/styles/tokens.css`, ready for the badges and the live wire.

## First live month (ATL-238)

The Autonomy turn-on sheet's `SheetNotice` has a new kind `{ kind: 'refused', message }`. A 422 with
`errors['changes.autonomy_level']` shows that message as the notice title (the API's own words, en/ar), with
`autonomy:err.refused.body` ("Nothing was saved. The level stays where it was."), `data-testid="au-sheet-refused"`.
The cap-clearing 422 on Money limits already surfaced through that page's existing field-error path.

## Deliberately not built (design pieces marked ATL-240 there)

- Disabling levels 3–4 and the clear-cap control on the live row while `first_live_month_ends_at` is set, and
  the `aup.firstMonth*` / `aup.pfWarn` / `aup.liveNoLine` / `aup.introLive` copy.
- The note's "Open Environment" button: there is no `/settings/environment` page in the SPA yet (ATL-239).
  Add it to `EnvScopeNotice` when that route exists.
- Sending `Accept-Language`: the SPA does not send it yet, so the 422 text is in whatever language Laravel picks.

## Tests

- `frontend/src/features/settings-risk/EnvScope.test.tsx` (Vitest, two-row fake backend): default and `?env=live`
  on all four panes, notice on/off, Risk/Money/Autonomy saves go to `env: 'live'` with the demo row unchanged,
  first-month refusal in en and ar, fresh demo accepts live autonomy 3.
- `frontend/e2e/settings-env-scope.spec.ts` (Playwright, mocked API, en and ar): the same, 14 tests.
