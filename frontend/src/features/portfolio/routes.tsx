import { ChartSpline } from 'lucide-react'
import { Navigate } from 'react-router'
import type { FeatureModule } from '../../app/feature'
import { OverviewPage } from './OverviewPage'

/*
 * Portfolio (brief §13). /portfolio is the landing page after sign-in: the index route
 * below wins over the shell's "first nav entry" fallback (docs/handoff/ATL-17-app-shell.md).
 */
const portfolio: FeatureModule = {
  routes: [
    { index: true, element: <Navigate to="/portfolio" replace /> },
    { path: 'portfolio', element: <OverviewPage /> },
  ],
  nav: [{ path: '/portfolio', labelKey: 'portfolio:nav.overview', order: 10, section: 'portfolio', icon: ChartSpline }],
}

export default portfolio
