import { NotebookPen } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* The Journal in the ⌘K palette (ATL-249). */
const palette: PaletteModule = {
  entries: [
    { id: 'page.journal', group: 'pages', area: 'journal', order: 10, to: '/journal', icon: NotebookPen, labelKey: 'shell:area.journal', keywords: 'post-mortem post-mortems lessons مراجعة دروس يوميات' },
  ],
}

export default palette
