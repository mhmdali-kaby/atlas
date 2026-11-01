import { ChartCandlestick } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { MarketsPage } from './MarketsPage'

/* Settings → Markets (brief §14.5, ATL-131): asset classes, volume and spread limits, US hours, watchlist size, allowed and blocked instruments. */
const settingsMarkets: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/markets', element: <MarketsPage /> }],
  nav: { path: '/settings/markets', labelKey: 'settings-markets:nav', order: 123, icon: ChartCandlestick },
}

export default settingsMarkets
