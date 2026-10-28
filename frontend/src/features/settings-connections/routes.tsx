import { Plug } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { ConnectionsPage } from './ConnectionsPage'

/* Settings → Connections (brief §14.2): the OKX demo connection, re-test, replace keys, remove. */
const settingsConnections: FeatureModule = {
  routes: [{ path: 'settings/connections', element: <ConnectionsPage /> }],
  nav: { path: '/settings/connections', labelKey: 'settings:nav.connections', order: 110, section: 'settings', icon: Plug },
}

export default settingsConnections
