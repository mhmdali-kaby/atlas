import { createBrowserRouter, type RouteObject } from 'react-router'
import type { FeatureModule } from './feature'
import { AppShell } from './AppShell'

const modules = import.meta.glob<{ default: FeatureModule }>('../features/*/routes.tsx', { eager: true })

export const features: FeatureModule[] = Object.values(modules).map((m) => m.default)

export const navEntries = features
  .flatMap((f) => (f.nav ? [f.nav] : []))
  .sort((a, b) => a.order - b.order)

const children: RouteObject[] = features.flatMap((f) => f.routes)

export const router = createBrowserRouter([{ path: '/', element: <AppShell />, children }])
