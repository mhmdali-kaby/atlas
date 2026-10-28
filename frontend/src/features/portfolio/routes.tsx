import { ChartSpline, Layers } from 'lucide-react'
import { Navigate } from 'react-router'
import type { FeatureModule } from '../../app/feature'
import { OverviewPage } from './OverviewPage'
import { PositionsPage } from './PositionsPage'

/*
 * Portfolio (brief §13). /portfolio is the landing page after sign-in: the index route
 * below wins over the shell's "first nav entry" fallback (docs/handoff/ATL-17-app-shell.md).
 */
const portfolio: FeatureModule = {
  routes: [
    { index: true, element: <Navigate to="/portfolio" replace /> },
    { path: 'portfolio', element: <OverviewPage /> },
    { path: 'portfolio/positions', element: <PositionsPage /> },
  ],
  nav: [
    { path: '/portfolio', labelKey: 'portfolio:nav.overview', order: 10, section: 'portfolio', icon: ChartSpline },
    { path: '/portfolio/positions', labelKey: 'portfolio:nav.positions', order: 20, section: 'portfolio', icon: Layers },
  ],
}

export default portfolio
