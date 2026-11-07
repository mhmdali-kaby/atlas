# Features

Each feature lives in its own folder (`src/features/portfolio/`, `src/features/connections/`, ...)
and exports a `routes.tsx` whose default export is a `FeatureModule` (see `src/app/feature.ts`).
`src/app/router.tsx` discovers them by glob, so adding a feature never edits a shared file.
Translations for a feature go in `src/i18n/locales/<lang>/<feature>.json`.

A feature that adds sidebar content declares its `area` (`warroom`, `portfolio` or `settings`) and
its `nav` links and/or `sidebar` sections; the shell shows them under that area's switcher entry.
See `docs/handoff/ATL-41-sidebar-areas.md`.
