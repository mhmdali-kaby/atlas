# ATL-19 — Onboarding steps 3–4: risk profile, autonomy, Finish → Portfolio

Built from design v2 `js/pages/onboarding.js` (risk(), autonomy()), `js/forms.js` (profileCards,
limitsCompare, autonomyList, mountRadios), `css/forms.css`, `css/components.css` (.choice).
Code: `frontend/src/features/onboarding/` — `Pickers.tsx`, `pickers.css`, `settings.ts`, `steps.tsx`
(RiskStep, AutonomyStep), `OnboardingPage.tsx`, `OnboardingGate.tsx`. Import from the barrel
`src/features/onboarding`.

## The two-minute version

- **New settings field `onboarding_completed`** (int 0–1, default 0) in `SettingsSchema::NUMERIC_FIELDS`.
  Finish setup PATCHes `{ autonomy_level, onboarding_completed: 1 }`. Without it, an owner who keeps
  Balanced + level 2 (the defaults) would make no-op PATCHes and the version would stay at 1 forever.
- **Onboarding is complete** = verified demo connection **and** `isOnboarded(doc)` =
  `version > 1 && settings.onboarding_completed === 1`. Version alone is not enough: Continue on the
  risk step already moves it to 2, and a reload on the autonomy step must not skip to Portfolio.
- `/onboarding` (any step) redirects to `/portfolio` once complete. `/` (OnboardingGate): no verified
  key → Welcome; verified but not complete → `?step=risk`; complete → first sidebar page. A failed
  settings lookup lets the owner through, like a failed connections lookup.
- **Saving happens on Continue / Finish** (as the design's busy state does), not on every card click.
  Continue shows the button spinner; a failure is an error toast and the step stays put.
- **409**: `usePatchSettings` refetches GET `/api/settings`, then sends the same `changes` once more on
  the fresh version. A second 409 throws (→ error toast).

## Reusable pieces (for ATL-21 Settings → Risk & Autonomy)

| Export | What it is |
| --- | --- |
| `RiskProfilePicker` | `{ value: RiskProfile, onChange(p), presets: Record<PresetId, RiskLimits>, customValues?: RiskLimits, className? }`. Three radio cards (four with `customValues` → "Custom" card showing those values). Each card: title, Default tag on Balanced, one-line text, max loss per trade / max position / daily loss / max leverage. |
| `LimitsCompare` | `{ presets, selected }` — the ten-limit table, the selected column washed. |
| `CompareDisclosure` | `{ children }` — "Compare all ten limits" / "Hide the comparison" disclosure (chevron mirrors in RTL). |
| `AutonomyPicker` | `{ value: number, onChange(level), enabled?: number[] }` — levels 1–4; levels not in `enabled` (default `[1, 2]`) are `aria-disabled` with a lock + "Available in a later phase". |
| `LimitValue`, `LIMIT_DEFS` | `"1.0%"`, `"5×"`, `"1.8 R"` per field (design limitDefs decimals); kept LTR inside RTL text. |
| `useSettings(env, enabled?)` | GET `/api/settings?env=` under query key `settingsKey(env)` = `['settings', env]`. |
| `usePatchSettings(env)` | Mutation taking a `changes` object; writes the 200 body into the cache. |
| `isOnboarded`, `isPreset`, `PRESET_IDS`, `DEFAULT_PROFILE`, `DEFAULT_AUTONOMY`, `ENABLED_AUTONOMY`, types `SettingsDoc`, `SettingsValues`, `RiskLimits`, `RiskProfile`, `PresetId`, `SettingsChanges` | from `settings.ts`. |

Keyboard model (both pickers): one tab stop on the checked card; Up/Down and Left/Right move **and
select** (Left/Right flip in RTL), Home/End jump; disabled levels are skipped. Space/Enter on a card
selects it (native button).

Strings: `onboarding.json` → `rp.*`, `lim.*`, `au.*` (`l1`…`l4`, `l1Text`…), `laterPhase`,
`risk.loading`, `risk.error.*`, `autonomy.note`, `saveError.*`, `complete.*`. ATL-21 can use them via
`useTranslation('onboarding')` or copy what it needs into its own namespace.

Also added: `formatFixed(value, digits, lang)` in `src/lib/format.ts` (plain decimal, no unit).

## Example: Finish setup request

```json
PATCH /api/settings
{ "env": "demo", "version": 2, "changes": { "autonomy_level": 2, "onboarding_completed": 1 } }
```

## Not built here / design gaps

- The design has no state for a failed save; it is an error toast ("Couldn’t save your choice").
- The "Setup complete — Atlas is syncing your OKX demo account." toast is from the design's finish.

## Tests

- `onboarding.test.tsx` → `describe('onboarding steps 3–4 (ATL-19)')` (10): profiles + API values +
  Balanced preselected; comparison table; keyboard choice of Conservative → PATCH → version 2; levels
  3/4 disabled with the later-phase note, arrows skip them; Finish → /portfolio with defaults kept,
  /onboarding redirects afterwards; level 1 saved; 409 retry once; double 409 → error; presets load
  error + Try again; `/` → risk step for a verified, unfinished owner; Arabic RTL arrows.
- `e2e/onboarding-steps.spec.ts` (5, Chromium): keyboard-only run of both steps and Finish in
  en/ar × light/dark, plus a 390px RTL check. Screenshots in `evidence/ATL-19/`.
- Pest: `SettingsApiTest` covers the new field (default 0, range 0–1, bool refused, a defaults-kept
  Finish still bumps the version).
