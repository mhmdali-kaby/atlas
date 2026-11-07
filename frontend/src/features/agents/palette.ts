import { Gauge, UsersRound } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* The Agents area in the ⌘K palette (ATL-249). */
const palette: PaletteModule = {
  entries: [
    { id: 'page.agents', group: 'pages', area: 'agents', order: 10, to: '/agents', icon: UsersRound, labelKey: 'agents:nav', keywords: 'team status budget claude وكلاء فريق' },
    { id: 'page.agents.scorecards', group: 'pages', area: 'agents', order: 20, to: '/agents/scorecards', icon: Gauge, labelKey: 'scorecards:nav', keywords: 'accuracy calibration brier score بطاقات أداء دقة' },
  ],
}

export default palette
