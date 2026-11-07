# ATL-227 — Settings → General, owner zone, preferences in the SPA

For ATL-230 (Schedule), ATL-232/233/234 and anyone printing a time.

## Two clocks (design v21 owner-setup §2)

- **Print a time** only through the owner-zone formatters: `features/warroom/time.ts`
  (`formatTime`, `formatClock`, `formatFullTime`, `formatDate`, `formatDateTime`…),
  `features/portfolio/time.ts`, or `ownerDateFormat(locale, options)` from `src/lib/ownerZone.ts`
  (cached per zone; never pass `timeZone` yourself).
- **A team day** (watchlist date, Replay date, "today" for limits): `teamDay(ms)` / `baghdadDay(ms)`.
  `TEAM_TZ` in `lib/ownerZone.ts` is the only place in `src/` that names Asia/Baghdad.
- A day given as `"YYYY-MM-DD"` is formatted with `timeZone: 'UTC'` at `T12:00:00Z`.
- Copy that puts a city after a time takes `{{city}}`: `ownerCity(lang)` in `warroom/time.ts`,
  or `zoneCity(zone, lang)` from `src/lib/zones.ts`.
- React that must re-render on a zone change: `useOwnerZone()`.

`src/lib/zones.ts`: `zoneCity`, `zoneName` (Intl longGeneric), `zoneOffset` ("GMT+3" / "غرينتش +3",
LTR isolate, true minus), `diffFromTeam(zone, at)` (minutes, negative = behind Baghdad),
`spanWords(min, lang)` (Arabic plurals), `minuteOfDay`, `browserZone`, `allZones`.

## Preferences (`src/lib/preferences.ts`)

- `usePreferences()` → `GET /api/me/preferences` (`['me','preferences']`).
- `PreferencesSync` (mounted in `RequireAuth`) applies language, theme and zone at boot.
- `useSavePreference().mutate({ field, value, previous })` PATCHes one field, applies it at once,
  rolls back screen + cache on error. `saveErrorOf(err, field)` → `{kind:'server', message}` (422)
  or `{kind:'offline'}`. The toolbar language button, appearance menu and More use it too; signed
  out (login page) they only change this browser.
- `api.ts` sends `Accept-Language: en|ar` (the current i18n language) on every request unless the
  caller set one.

## DayLine (shared, `features/settings-general/DayLine.tsx`)

```tsx
<DayLine values={scheduleFrom(settingsDoc.settings)} frame="team" now={Date.now()} lang={lang} highlight="analysis" />
```

`frame="team"` draws Baghdad's 00:00–24:00 (Schedule); `"owner"` the owner's (General, onboarding).
`highlight`: `brief | regime | watchlist | report | analysis`. Pure maths is in `dayModel.ts`
(`scheduleFrom`, `slots`, `usSession`, `dayItems`, `segments`, `placeLabels`, `clock`, `hm`) —
Schedule's "Cycles at 08:10, 08:40 … 22:40" preview can reuse `slots()`. `scheduleFrom` keeps a
default for any key that does not parse, so pass it the values being edited.

## Recorded flows

`e2e/flows/appearance.ts` now exports `seedAppearance(page, lang, theme)`: it seeds localStorage
and answers the boot `GET /api/me/preferences` with that lang/theme, because the parallel projects
share one real owner. Use it in any flow that needs a language or theme.

## Not done here

- The rest of design §2's copy table ("Resets at 00:00 Baghdad" in Money limits, Agents,
  Scorecards, Portfolio History/Performance, Journal, Data): those strings still say Baghdad.
  The War Room's ("{time} {city}, {utc} UTC", Replay line, watchlist US band, /block) are done.
- The grouped Settings sidebar (10 px gaps between groups, §6).
