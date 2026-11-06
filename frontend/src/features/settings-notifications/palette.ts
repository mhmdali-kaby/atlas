import { Bell } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.notifications', group: 'pages', area: 'settings', order: 150, to: '/settings/notifications', icon: Bell, labelKey: 'settings-notifications:nav', keywords: 'telegram alerts إشعارات تنبيهات' },
  ],
}

export default palette
