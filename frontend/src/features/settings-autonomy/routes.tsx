import { Route } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { AutonomyPage } from './AutonomyPage'

/* Settings → Autonomy (brief §14.6, decision #254, ATL-216): the level, the ask-above amount. */
const settingsAutonomy: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/autonomy', element: <AutonomyPage /> }],
  nav: { path: '/settings/autonomy', labelKey: 'settings-autonomy:nav', order: 124, icon: Route },
}

export default settingsAutonomy
