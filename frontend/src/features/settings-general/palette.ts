import { Settings } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Settings → this page in the ⌘K palette (ATL-249). The label is the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.settings.general', group: 'pages', area: 'settings', order: 100, to: '/settings/general', icon: Settings, labelKey: 'settings-general:nav', keywords: 'name language time zone theme two-factor 2fa اسم لغة مظهر تحقق' },
  ],
}

export default palette
