import { ArrowLeftRight, ChessKnight, CircleCheck, ListChecks, NotebookPen, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { MomentKind } from './replay'

/** Each Replay moment kind's glyph, on the day line's markers and in the inspector's list (design v18). */
export const MOMENT_ICON: Record<MomentKind, LucideIcon> = {
  watchlist: ListChecks,
  proposal: ChessKnight,
  order: ArrowLeftRight,
  fill: CircleCheck,
  system_alert: TriangleAlert,
  post_mortem: NotebookPen,
}
