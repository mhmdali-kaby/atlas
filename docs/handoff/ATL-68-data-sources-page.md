# ATL-68 — Settings → Data sources page (for ATL-69's keys)

`frontend/src/features/settings-data-sources/`, route `/settings/sources`, Settings sidebar item 4
(order 140, `Rss` icon). Strings: `src/i18n/locales/{en,ar}/settings-data-sources.json` (namespace
`settings-data-sources`). Built from design v6 §5.1–5.5, §5.7, §6, §7 against ATL-66/ATL-71's API
(`docs/handoff/ATL-66-data-sources-api.md`).

## Files

| file | what |
|---|---|
| `api.ts` | `useSources()` (GET `/api/data-sources`, key `SOURCES_KEY = ['data-sources']`, refetch 30 s while visible), `useToggleSource(onFailed)` (optimistic PATCH `{enabled}`; rollback + `onFailed(vars, 'limited' \| 'down')`; invalidates on settle), `failureOf(err)` |
| `model.ts` | Pure rules: `rowStatus` (design §7.2 precedence: needs_key > off > standby > retrying > starting > failing > never_run > working), `summary` (healthy/trouble/noneWorking/firstRun/stalled/allOff, trouble list, `down` kinds, `missingKeys`), `withEnabled` (the optimistic cache edit: recomputes `status` and `summary.*.all_down`, clears `covered_by` on sources a disabled coverer covered), `startedBy`, `wire`, `usSessions`, `monogram`, `PROVIDER_NAMES` / `PROVIDER_SITES`, `missingKeyProviders` |
| `SourcesPage.tsx` | Page, loading/error/empty, groups; owns `startedAt` (key → ms when the owner turned it on; a row shows Starting until `last_success_at` is newer) |
| `Summary.tsx` | Summary head, missing-keys line, outage notices (`role="alert"`, `data-testid="src-down"`, `data-cat`), the wire (hover, ←/→/Home/End/Esc, live region, hidden table) |
| `SourceRow.tsx` | Monogram, name, status icon + word + detail (`#src-sub-<key>`), error line, strip + count, switch |
| `testFixtures.ts` | `source()`, `report()`, `mixed(now)` builders (used by vitest and the e2e spec) |

## What ATL-69 adds (deliberately not built here)

- **The Keys group** (design §5.6), last on the page, after the sentiment group. Group by `provider` over
  `requires_key` sources; `has_key` / `key_last4` are the same on every source of a provider.
- **Add key buttons.** The design puts one in a needs_key row's yield column (`.src-yield`, currently empty
  for needs_key), one after the summary's missing-keys line (`data-testid="src-sum-keys"`; one provider →
  "Add key" opening that sheet, two → "Add keys" scrolling to the Keys group), and **Replace key…** at the end
  of a keyed source's error line (`.src-error`). I left all three out rather than ship buttons that open
  nothing; the text notice is there (brief §7.2).
- **Key sheet and remove sheet**, and their 422/429/500 answers. After a PUT succeeds, set the provider's
  sources into `startedAt` the same way `onToggle` does (so they show Starting), and invalidate `SOURCES_KEY`.
  Strings for all of this are in the prototype (`design/v6/atlas/js/pages/sources.js`, keys `src.keys.*`,
  `src.ks.*`, `src.rm.*`); add them to the same JSON files.

## Things that are easy to get wrong

- Status text is the signal, not a pill (design §2 decision). Tests look for `data-status` on
  `[data-testid="src-row"]` and the words in `[data-testid="src-status"]`.
- Stalled summary: working rows show a neutral clock, not the green check (TL review of v6).
- At ≤ 640 px the status detail and the error wrap to two lines instead of an ellipsis (TL review of v6).
- An Off row's monogram fades only its tile, not its letters: 55 % opacity on the whole tile failed axe
  colour-contrast in every theme.
- The outage notice reads the API's `summary.<cat>.all_down`; `withEnabled` keeps it right during an
  optimistic toggle. Notices never show in allOff or stalled.
- Names: `name.<key with . → _>` in the JSON, else the API `name`.

## Tests

- `model.test.ts`: every rule above, US market hours across the 1 Nov DST change, no band at weekends.
- `SourcesPage.test.tsx`: statuses, optimistic toggle with a 500 rollback and toast, Starting, notice,
  error/empty states.
- `e2e/flows/settings-data-sources.appearance.spec.ts`: runs in ltr/rtl × light/dark with `/api/data-sources`
  routed in the browser; bounds check at 1280 and 390 px; axe (serious/critical) on the page.
  Screenshots go to `evidence/ATL-68/`.
