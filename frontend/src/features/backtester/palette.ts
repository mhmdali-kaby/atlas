import { FlaskConical } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* The Backtester in the ⌘K palette (ATL-249). */
const palette: PaletteModule = {
  entries: [
    { id: 'page.backtester', group: 'pages', area: 'backtester', order: 10, to: '/backtester', icon: FlaskConical, labelKey: 'backtester:title', keywords: 'backtest rule strategy اختبار استراتيجية' },
  ],
}

export default palette
