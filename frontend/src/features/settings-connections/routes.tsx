import { Plug } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { ConnectionsPage } from './ConnectionsPage'

/* Settings → Connections (brief §14.2): the OKX demo connection, re-test, replace keys, remove. */
const settingsConnections: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/connections', element: <ConnectionsPage /> }],
  nav: { path: '/settings/connections', labelKey: 'settings:nav.connections', order: 110, icon: Plug },
}

export default settingsConnections
