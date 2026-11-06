# ATL-233: onboarding Data sources and Market history steps

For ATL-234, which adds Claude login and "Meet your team" to the same wizard.

## Step list

`frontend/src/features/onboarding/paths.ts`:

```ts
export const STEPS = ['welcome', 'connect', 'sources', 'risk', 'autonomy', 'backfill'] as const
```

The URL carries the step (`/onboarding?step=sources`, `?step=backfill`). The stepper and both "Step n of N"
lines (the screen-reader line `data-testid="ob-step-of"` and the phone line) read `STEPS.length`, so adding
`claude` (after `connect`) and `team` (after `backfill`) to this array is all the indicator needs. The
`stepOf` string is `"Step <f>{{n}}</f> of <f>{{total}}</f>"` (Trans, LTR islands). Step names live under
`steps.*` in `i18n/locales/{en,ar}/onboarding.json`.

Stepper layout: every label from 1100 px; below that only the current label (`max-[1100px]:hidden`);
at 600 px and below the numbered `ol.ob-steps-ol` is replaced by `.ob-steps-sm` ("Step 3 of 6", the
name, and an N-part bar built with `grid-auto-flow: column`, so it takes any N). With 8 steps, check the
1100 px threshold still fits at 1280.

## What each Continue does (OnboardingPage.tsx `onContinue`)

| Step | Continue | Footer extra |
|---|---|---|
| sources | `sourcesRef.current.save()`; moves to the next step only if it returns `true` | **Skip** (`data-testid="ob-skip"`) moves on, saves nothing |
| risk | `PATCH /api/settings {risk_profile}` then next | |
| autonomy | `PATCH /api/settings {autonomy_level[, autonomy_ask_above_usd if 3]}` then next | |
| last step (backfill today) | `PATCH /api/settings {onboarding_completed: 1}` then navigate to `AFTER_ONBOARDING_PATH` (`/portfolio`) | |

Autonomy used to be saved together with `onboarding_completed` on Finish; it is now saved on its own
Continue. The Finish PATCH is just `{ onboarding_completed: 1 }`, so once `team` becomes last, the backfill
step's Continue goes through the generic `go(STEPS[index + 1])` path with no change. ATL-234 changes
`AFTER_ONBOARDING_PATH` to `/warroom`.

`canContinue`: sources needs a verified key and no key save in flight; backfill (and every step after
Connect) needs a verified key and the settings doc loaded. **Backfill progress never gates it**, at 0% too.

## Data sources step (`SourcesStep.tsx`)

Reuses Settings → Data sources: `useSources()` (`GET /api/data-sources`, polled 30 s), `useToggleSource`
(`PATCH /api/data-sources/{key} {enabled}`, optimistic, toast on failure), `putKey(sourceKey, value)`
(`PUT /api/data-sources/{key}/key {api_key}` → `{ data: ApiSource }` with `key_last4`), `withKey` for the
cache edit, and `KeySheet` for **Replace** on an already-saved key.

The key fields are uncontrolled; the step keeps only "is this field filled" in state (ATL-69: the typed
key never sits in React state or a TanStack cache). `save()` PUTs each filled field to the provider's first
keyed source (the API applies it provider-wide), clears it, and toasts "Key saved"/"Keys saved". A 422/429/
other failure marks that field (`sources.invalid|limited|down`), focuses it and returns `false`. A saved key
shows as `•••• last4` (`data-testid="ob-key-mask"`, accessible name "Saved key ending in 9XyZ").

The summary ("6 of 10 sources work without a key" / "9 of 10 sources will work") follows what is typed,
before any save.

## Market history step (`BackfillStep.tsx`)

`useBackfill(Number.MAX_SAFE_INTEGER)` from settings-data, so it polls `GET /api/data/backfill?env=demo`
every 5 s the whole time the step is open (Settings → Data polls only while a job runs). It reuses
`buildModel`, `summaryState`, the `title.*`/`sub.*`/`class.*`/`st.*` strings of the settings-data
namespace, and the `.dt-map`/`.cell` styles from `settings-data/data.css`. `useEtaText` moved to
`settings-data/eta.ts` so both pages share it.

Test ids: `backfill-bar` (progressbar, `aria-valuenow` floored percent), `backfill-insts` ("14 of 46
instruments done"), `backfill-map`, `backfill-carry`. States: loading skeleton, load error (warning notice
with Try again; Continue still enabled), not started (0%), running, paused, stalled, done.

## Tests

- Vitest: `src/features/onboarding/onboarding.test.tsx`, block "Data sources and Market history steps
  (ATL-233)". Its `fakeBackend` now also serves `/api/data-sources` (GET, PATCH, PUT key, `keyRefused`
  option) and `/api/data/backfill?env=demo` (`backend.backfill = backfillReport(done, total)`).
- Playwright (mocked): `e2e/onboarding-sources-backfill.spec.ts`, 1280 and 390 px × en/ar × light/dark.
- `e2e/onboarding-steps.spec.ts` and the real-stack `e2e/flows/onboarding.spec.ts` walk the new steps.
  ATL-234 updates the flow spec for Claude, Team and `/warroom`.

## Not built here

The design's Claude and "Meet your team" steps (ATL-234). The gate (`OnboardingGate`) still sends a
verified owner who has not finished to `?step=risk`.
