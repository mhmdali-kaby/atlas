import type { FeatureModule } from '../../app/feature'
import { BacktesterPage } from './BacktesterPage'
import { RunPage } from './RunPage'
import { RunsSidebar } from './RunsSidebar'

/*
 * The Backtester (ATL-209 form, ATL-210 results; decision #253 L; design v18): its own area between
 * the Journal and Settings. The sidebar is one section: "New backtest" and the recent runs. There is
 * no nav entry, so the toolbar reads "Backtester" on every page and only the open run is current.
 */
const backtester: FeatureModule = {
  area: 'backtester',
  routes: [
    { path: 'backtester', element: <BacktesterPage /> },
    { path: 'backtester/run', element: <RunPage /> },
  ],
  sidebar: { id: 'runs', order: 10, component: RunsSidebar },
}

export default backtester
