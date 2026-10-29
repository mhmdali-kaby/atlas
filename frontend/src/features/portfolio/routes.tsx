import { ArrowLeftRight, ChartSpline, Layers, Wallet } from 'lucide-react'
import { Navigate } from 'react-router'
import type { FeatureModule } from '../../app/feature'
import { CashflowsPage } from './CashflowsPage'
import { HoldingsPage } from './HoldingsPage'
import { OverviewPage } from './OverviewPage'
import { PositionsPage } from './PositionsPage'

/*
 * Portfolio (brief §13). /portfolio is the landing page after sign-in: the index route
 * below wins over the shell's "first nav entry" fallback (docs/handoff/ATL-17-app-shell.md).
 */
const portfolio: FeatureModule = {
  area: 'portfolio',
  routes: [
    { index: true, element: <Navigate to="/portfolio" replace /> },
    { path: 'portfolio', element: <OverviewPage /> },
    { path: 'portfolio/positions', element: <PositionsPage /> },
    { path: 'portfolio/holdings', element: <HoldingsPage /> },
    { path: 'portfolio/cashflows', element: <CashflowsPage /> },
  ],
  nav: [
    { path: '/portfolio', labelKey: 'portfolio:nav.overview', order: 10, icon: ChartSpline },
    { path: '/portfolio/positions', labelKey: 'portfolio:nav.positions', order: 20, icon: Layers },
    { path: '/portfolio/holdings', labelKey: 'portfolio:nav.holdings', order: 30, icon: Wallet },
    { path: '/portfolio/cashflows', labelKey: 'portfolio:nav.cashflows', order: 40, icon: ArrowLeftRight },
  ],
}

export default portfolio
