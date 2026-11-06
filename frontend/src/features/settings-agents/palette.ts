import { UsersRound } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.agents', group: 'pages', area: 'settings', order: 125, to: '/settings/agents', icon: UsersRound, labelKey: 'settings-agents:nav', keywords: 'model budget agent settings نموذج ميزانية' },
  ],
}

export default palette
