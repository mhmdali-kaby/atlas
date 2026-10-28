import { createBrowserRouter, type RouteObject } from 'react-router'
import { RequireAuth } from '../features/auth/RequireAuth'
import type { FeatureModule } from './feature'
import { AppShell } from './AppShell'

const modules = import.meta.glob<{ default: FeatureModule }>('../features/*/routes.tsx', { eager: true })

export const features: FeatureModule[] = Object.values(modules).map((m) => m.default)

export const navEntries = features
  .flatMap((f) => (f.nav ? [f.nav] : []))
  .sort((a, b) => a.order - b.order)

const children: RouteObject[] = features.flatMap((f) => f.routes)
const publicRoutes: RouteObject[] = features.flatMap((f) => f.publicRoutes ?? [])

/** Public routes first; everything else lives in the shell behind the session guard. */
export const routes: RouteObject[] = [
  ...publicRoutes,
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    // The catch-all keeps unknown paths behind the guard too: signed out, any URL goes to
    // /login?next=… (a page whose feature is not built yet included); signed in, an empty shell.
    children: [...children, { path: '*', element: null }],
  },
]

export const router = createBrowserRouter(routes)
