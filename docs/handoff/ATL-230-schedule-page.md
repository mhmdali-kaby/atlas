# ATL-230 — Settings → Schedule page

`frontend/src/features/settings-schedule/`, route `/settings/schedule`, nav order 155 (after
Notifications, before Environment). Locale namespace `settings-schedule`.

## What it reads and writes

- `GET /api/settings?env=<active>` — the 12 `schedule.*` keys from ATL-228. A missing key falls
  back to `DEFAULTS` in `model.ts` (same values as config/defaults.yaml).
- `PATCH /api/settings` `{ env, version, changes }` with only the changed `schedule.*` keys; the
  interval goes as a JSON integer, quiet hours on/off as a JSON bool.
- `GET /api/settings/history` — Activity shows the 5 newest versions that touch `schedule.*`
  (plus version 1 as "Schedule created…"); other keys in a version read "Other settings changed".
- The owner's zone is `useOwnerZone()` from `lib/ownerZone.ts` (kept current by PreferencesSync);
  `diffFromTeam`, `zoneCity(zone, lang)` and `spanWords` come from `lib/zones.ts`.
- The env is `useTradingEnv()` (the active env). There is no Demo | Live control on this page.

## Behaviour

- Edits batch behind the save bar. The client refuses only text that is not a time (shown when the
  field is left) or an interval that is not a number. Order and range rules (watchlist after the
  07:30 jobs, first slot before last, 15–240, quiet start ≠ end) are the server's. Its 422
  `changes.schedule.<key>` message shows under that field, and any edit clears the messages.
- 409: refetches, shows the other window's version, and lists the owner's edits with "Apply my
  changes again" (re-applies only the changed fields on top of the new version) or "Discard".
- Preview: `previewSlots(values)` guards the interval (15–240) and first < last, then uses
  `settings-general/dayModel.ts`'s `slots` (first + k × interval up to and including last, the
  same rule as ATL-229). `hm`, `clock`, `DAY_MIN`, `SCHEDULE_DEFAULTS` and `scheduleFrom` also come
  from dayModel; `model.ts` only adds the weekly review and weekend keys.
- The hero is ATL-227's `<DayLine frame="team">`, fed the draft (an unusable interval or slot
  order falls back, as the design's `lineValues`), so it changes before saving. `previewTimes` shows the first two, "…", the last.
- Quiet hours that cross midnight read "Until 07:00 the next day." under To.
