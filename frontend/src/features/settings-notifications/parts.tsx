import {
  Bot,
  CheckCheck,
  FileText,
  Hand,
  Newspaper,
  OctagonAlert,
  OctagonX,
  Scale,
  ShieldX,
  Target,
  WifiOff,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { cx } from '../../ui/cx'
import type { EventId, Tone } from './model'

/** The glyph that names each event (design §5.2): colour is never the only signal. */
export const EVENT_ICON: Record<EventId, LucideIcon> = {
  proposal_awaiting_approval: Hand,
  circuit_breaker: OctagonAlert,
  kill_switch: OctagonX,
  reconciliation_mismatch: Scale,
  order_filled: CheckCheck,
  stop_hit: ShieldX,
  target_hit: Target,
  news_alert: Newspaper,
  agent_degraded: Bot,
  data_stale: WifiOff,
  daily_report: FileText,
}

export function EventTile({ event, tone }: { event: EventId; tone: Tone }) {
  const Icon = EVENT_ICON[event]
  return (
    <span className={cx('ns-tile', tone !== 'neutral' && `is-${tone}`)} aria-hidden="true">
      <Icon strokeWidth={1.5} />
    </span>
  )
}

/** A figure, ID or `•••• last4` inside running text: always an LTR isolate (design §7). */
export function Fig({ children }: { children: ReactNode }) {
  return (
    <bdi dir="ltr" className="tabular-nums">
      {children}
    </bdi>
  )
}
