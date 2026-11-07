import { ArrowLeftRight } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { EnvironmentPage } from './EnvironmentPage'

/* Settings → Environment (brief §14.11, decision 255 (5), ATL-239): pre-flight and the demo/live switch. Last in Settings. */
const settingsEnvironment: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/environment', element: <EnvironmentPage /> }],
  nav: { path: '/settings/environment', labelKey: 'settings-environment:nav', order: 190, icon: ArrowLeftRight },
}

export default settingsEnvironment
