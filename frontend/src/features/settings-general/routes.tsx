import { Settings } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { GeneralPage } from './GeneralPage'

/* Settings → General (brief §14.1, ATL-227): first in the Settings sidebar, so the area opens on it. */
const settingsGeneral: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/general', element: <GeneralPage /> }],
  nav: { path: '/settings/general', labelKey: 'settings-general:nav', order: 100, icon: Settings },
}

export default settingsGeneral
