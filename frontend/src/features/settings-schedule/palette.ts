import { CalendarClock } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.schedule', group: 'pages', area: 'settings', order: 155, to: '/settings/schedule', icon: CalendarClock, labelKey: 'settings-schedule:nav', keywords: 'cycle times quiet hours جدول مواعيد' },
  ],
}

export default palette
