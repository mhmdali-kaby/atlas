import { createBrowserRouter, type RouteObject } from 'react-router'
import { RequireAuth } from '../features/auth/RequireAuth'
import { AppShell, HomeRedirect, NotFound } from './AppShell'
import { features } from './registry'

export { features, navEntries } from './registry'

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
    // Feature routes first, so a feature's own index route wins over the fallback "/"
    // (the first sidebar page). The catch-all keeps unknown paths behind the guard too:
    // signed out, any URL goes to /login?next=…; signed in, a "nothing here yet" page.
    children: [...children, { index: true, element: <HomeRedirect /> }, { path: '*', element: <NotFound /> }],
  },
]

export const router = createBrowserRouter(routes)
