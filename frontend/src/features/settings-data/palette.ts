import { Database } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.data', group: 'pages', area: 'settings', order: 130, to: '/settings/data', icon: Database, labelKey: 'settings-data:nav.data', keywords: 'backfill history candles بيانات تاريخ' },
  ],
}

export default palette
