# Features

Each feature lives in its own folder (`src/features/portfolio/`, `src/features/connections/`, ...)
and exports a `routes.tsx` whose default export is a `FeatureModule` (see `src/app/feature.ts`).
`src/app/router.tsx` discovers them by glob, so adding a feature never edits a shared file.
Translations for a feature go in `src/i18n/locales/<lang>/<feature>.json`.
