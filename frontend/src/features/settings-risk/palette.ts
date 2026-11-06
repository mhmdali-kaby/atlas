import { Shield } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.risk', group: 'pages', area: 'settings', order: 120, to: '/settings/risk', icon: Shield, labelKey: 'settings:nav.risk', keywords: 'risk profile leverage loss limit رافعة خسارة' },
  ],
}

export default palette
