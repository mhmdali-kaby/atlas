import { Rss } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.sources', group: 'pages', area: 'settings', order: 140, to: '/settings/sources', icon: Rss, labelKey: 'settings-data-sources:nav', keywords: 'news feeds finnhub cryptopanic مصادر أخبار' },
  ],
}

export default palette
