import { ChartCandlestick } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.markets', group: 'pages', area: 'settings', order: 123, to: '/settings/markets', icon: ChartCandlestick, labelKey: 'settings-markets:nav', keywords: 'whitelist blocklist asset class قائمة أسواق' },
  ],
}

export default palette
