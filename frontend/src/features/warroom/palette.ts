import { History, MessagesSquare } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* The War Room and its Replay in the ⌘K palette (ATL-249). */
const palette: PaletteModule = {
  entries: [
    { id: 'page.warroom', group: 'pages', area: 'warroom', order: 10, to: '/warroom', icon: MessagesSquare, labelKey: 'shell:area.warroom', keywords: 'chat team debate feed غرفة عمليات فريق نقاش' },
    { id: 'page.warroom.replay', group: 'pages', area: 'warroom', order: 20, to: '/warroom/replay', icon: History, labelKey: 'command-palette:page.replay', keywords: 'replay yesterday timeline past day إعادة عرض أمس' },
  ],
}

export default palette
