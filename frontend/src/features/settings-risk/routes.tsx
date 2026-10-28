import { Shield } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { RiskPage } from './RiskPage'

/* Settings → Risk & Autonomy (brief §14.3, §14.6): profile, the ten limits, autonomy, Activity. */
const settingsRisk: FeatureModule = {
  routes: [{ path: 'settings/risk', element: <RiskPage /> }],
  nav: { path: '/settings/risk', labelKey: 'settings:nav.risk', order: 120, section: 'settings', icon: Shield },
}

export default settingsRisk
