import type { FeatureModule } from '../../app/feature'
import { AgentsPage } from './AgentsPage'
import { TeamSidebar } from './TeamSidebar'

/*
 * Agents (ATL-117; brief §12, §14.7; design v9): its own area between the War Room and Portfolio.
 * The sidebar lists the team; each row jumps to the agent's row on the page.
 */
const agents: FeatureModule = {
  area: 'agents',
  routes: [{ path: 'agents', element: <AgentsPage /> }],
  sidebar: { id: 'team', order: 10, component: TeamSidebar },
}

export default agents
