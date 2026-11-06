import { Banknote } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.money', group: 'pages', area: 'settings', order: 122, to: '/settings/money', icon: Banknote, labelKey: 'settings-money:nav', keywords: 'capital cap usdt per trade رأس المال حدود' },
  ],
}

export default palette
