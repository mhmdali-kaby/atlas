import { Database } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { DataPage } from './DataPage'

/* Settings → Data (brief §14.10, ATL-49): backfill progress, data quality, storage, Re-sync now. */
const settingsData: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/data', element: <DataPage /> }],
  nav: { path: '/settings/data', labelKey: 'settings-data:nav.data', order: 130, icon: Database },
}

export default settingsData
