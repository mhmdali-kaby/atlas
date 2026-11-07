import { ArrowLeftRight } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.environment', group: 'pages', area: 'settings', order: 190, to: '/settings/environment', icon: ArrowLeftRight, labelKey: 'settings-environment:nav', keywords: 'live demo switch trade live حقيقي تجريبي بيئة' },
  ],
}

export default palette
