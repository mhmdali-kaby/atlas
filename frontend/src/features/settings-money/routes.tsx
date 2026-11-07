import { Banknote } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { MoneyPage } from './MoneyPage'

/* Settings → Money limits (brief §14.4, ATL-130): four USDT caps and the live "next trade can risk" preview. */
const settingsMoney: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/money', element: <MoneyPage /> }],
  nav: { path: '/settings/money', labelKey: 'settings-money:nav', order: 122, icon: Banknote },
}

export default settingsMoney
