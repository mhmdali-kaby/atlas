import { Activity, Gauge } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { AgentsPage } from './AgentsPage'
import { ScorecardsPage } from './scorecards/ScorecardsPage'
import { TeamSidebar } from './TeamSidebar'

/*
 * Agents (ATL-117; brief §12, §14.7; design v9): its own area between the War Room and Portfolio.
 * Two pages, Status and Scorecards (ATL-201, design v16 §5), above the team list; each team row
 * jumps to that agent on whichever page is open.
 */
const agents: FeatureModule = {
  area: 'agents',
  routes: [
    { path: 'agents', element: <AgentsPage /> },
    { path: 'agents/scorecards', element: <ScorecardsPage /> },
  ],
  nav: [
    { path: '/agents', labelKey: 'scorecards:navStatus', order: 1, icon: Activity },
    { path: '/agents/scorecards', labelKey: 'scorecards:nav', order: 2, icon: Gauge },
  ],
  sidebar: { id: 'team', order: 10, component: TeamSidebar },
}

export default agents
