import { Plug } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.connections', group: 'pages', area: 'settings', order: 110, to: '/settings/connections', icon: Plug, labelKey: 'settings:nav.connections', keywords: 'okx api key exchange مفتاح منصة' },
  ],
}

export default palette
