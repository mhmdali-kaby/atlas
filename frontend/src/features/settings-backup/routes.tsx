import { DatabaseBackup } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { BackupPage } from './BackupPage'

/* Settings → Backup (decision 255 (3), ATL-232): backup status, folder/time/keep, Back up now, restore steps. */
const settingsBackup: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/backup', element: <BackupPage /> }],
  nav: { path: '/settings/backup', labelKey: 'settings-backup:nav', order: 145, icon: DatabaseBackup },
}

export default settingsBackup
