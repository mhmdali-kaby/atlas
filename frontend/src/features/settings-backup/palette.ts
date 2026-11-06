import { DatabaseBackup } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.backup', group: 'pages', area: 'settings', order: 145, to: '/settings/backup', icon: DatabaseBackup, labelKey: 'settings-backup:nav', keywords: 'pg_dump restore نسخ احتياطي استعادة' },
  ],
}

export default palette
