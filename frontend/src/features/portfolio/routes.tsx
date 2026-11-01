import { ArrowLeftRight, ChartSpline, History, Layers, ListChecks, Wallet } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { CashflowsPage } from './CashflowsPage'
import { HistoryPage } from './HistoryPage'
import { HoldingsPage } from './HoldingsPage'
import { OrdersPage } from './OrdersPage'
import { OverviewPage } from './OverviewPage'
import { PositionsPage } from './PositionsPage'

/*
 * Portfolio (brief §13). Finishing onboarding lands on /portfolio; "/" opens the War Room
 * (design v4 War Room §3), the first area.
 */
const portfolio: FeatureModule = {
  area: 'portfolio',
  routes: [
    { path: 'portfolio', element: <OverviewPage /> },
    { path: 'portfolio/positions', element: <PositionsPage /> },
    { path: 'portfolio/orders', element: <OrdersPage /> },
    { path: 'portfolio/holdings', element: <HoldingsPage /> },
    { path: 'portfolio/cashflows', element: <CashflowsPage /> },
    { path: 'portfolio/history', element: <HistoryPage /> },
  ],
  nav: [
    { path: '/portfolio', labelKey: 'portfolio:nav.overview', order: 10, icon: ChartSpline },
    { path: '/portfolio/positions', labelKey: 'portfolio:nav.positions', order: 20, icon: Layers },
    { path: '/portfolio/orders', labelKey: 'portfolio-orders:nav', order: 25, icon: ListChecks },
    { path: '/portfolio/holdings', labelKey: 'portfolio:nav.holdings', order: 30, icon: Wallet },
    { path: '/portfolio/cashflows', labelKey: 'portfolio:nav.cashflows', order: 40, icon: ArrowLeftRight },
    { path: '/portfolio/history', labelKey: 'portfolio-history:nav', order: 50, icon: History },
  ],
}

export default portfolio
