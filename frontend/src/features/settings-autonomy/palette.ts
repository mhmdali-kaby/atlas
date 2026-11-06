import { Route } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.autonomy', group: 'pages', area: 'settings', order: 124, to: '/settings/autonomy', icon: Route, labelKey: 'settings-autonomy:nav', keywords: 'auto approve level استقلالية موافقة' },
  ],
}

export default palette
