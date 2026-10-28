import type { RouteObject } from 'react-router'

/** What every src/features/<name>/routes.tsx default-exports. */
export interface FeatureModule {
  /** Routes mounted inside the app shell. */
  routes: RouteObject[]
  /** Optional sidebar entry; `labelKey` is an i18n key (namespace:key). */
  nav?: { path: string; labelKey: string; order: number }
}
