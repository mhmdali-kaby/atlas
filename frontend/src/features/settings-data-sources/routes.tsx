import { Rss } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { SourcesPage } from './SourcesPage'

/* Settings → Data sources (brief §7.2, ATL-68): every news, calendar and sentiment source, its health and an on/off switch. */
const settingsDataSources: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/sources', element: <SourcesPage /> }],
  nav: { path: '/settings/sources', labelKey: 'settings-data-sources:nav', order: 140, icon: Rss },
}

export default settingsDataSources
