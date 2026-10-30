import { UsersRound } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { AgentsSettingsPage } from './AgentsSettingsPage'

/* Settings → Agents (brief §14.7, ATL-118): enable agents, pick models, set daily budgets, edit the web site lists. */
const settingsAgents: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/agents', element: <AgentsSettingsPage /> }],
  nav: { path: '/settings/agents', labelKey: 'settings-agents:nav', order: 125, icon: UsersRound },
}

export default settingsAgents
