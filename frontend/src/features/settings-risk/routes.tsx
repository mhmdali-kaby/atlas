import { Shield } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { RiskPage } from './RiskPage'

/* Settings → Risk (brief §14.3): profile, the ten limits, Activity. Autonomy has its own pane (ATL-216). */
const settingsRisk: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/risk', element: <RiskPage /> }],
  nav: { path: '/settings/risk', labelKey: 'settings:nav.risk', order: 120, icon: Shield },
}

export default settingsRisk
