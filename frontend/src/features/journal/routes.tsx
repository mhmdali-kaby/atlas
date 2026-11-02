import { NotebookPen } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { JournalPage } from './JournalPage'

/*
 * The Journal (ATL-202; brief §5 journal_entries, §16 /api/journal; design §7): a fifth area between
 * Portfolio and Settings whose sidebar has one item, Post-mortems.
 */
const journal: FeatureModule = {
  area: 'journal',
  routes: [{ path: 'journal', element: <JournalPage /> }],
  nav: { path: '/journal', labelKey: 'journal:nav', order: 10, icon: NotebookPen },
}

export default journal
