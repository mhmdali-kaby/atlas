import type { TFunction } from 'i18next'
import {
  Bell,
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
import { formatDate, formatTime } from '../warroom/time'
import { formatDecimal, formatMoney, type Lang } from '../../lib/format'
import type { NotificationRow } from './api'
import { isEventId, iso, toneOf, type EventId, type Tone } from './events'

/*
 * One title, body and meta line per row, built only from `event` and `params` (design v18 §5.2),
 * the same text in the toast, the list and the browser banner. Figures, instrument IDs and times
 * are LTR isolates so they read correctly inside Arabic; the two untrusted strings (a news headline
 * and its source) are first-strong isolates and are only ever rendered as text.
 */

/** What each event adds to the shared catalogue (./events): its icon and its row action. */
const EXTRA: Record<EventId, { icon: LucideIcon; action: string }> = {
  proposal_awaiting_approval: { icon: Hand, action: 'review' },
  circuit_breaker: { icon: OctagonAlert, action: 'warroom' },
  kill_switch: { icon: OctagonX, action: 'warroom' },
  reconciliation_mismatch: { icon: Scale, action: 'positions' },
  order_filled: { icon: CheckCheck, action: 'order' },
  stop_hit: { icon: ShieldX, action: 'position' },
  target_hit: { icon: Target, action: 'position' },
  news_alert: { icon: Newspaper, action: 'news' },
  agent_degraded: { icon: Bot, action: 'agents' },
  data_stale: { icon: WifiOff, action: 'data' },
  daily_report: { icon: FileText, action: 'report' },
}

const BREAKERS = ['daily_loss', 'weekly_loss', 'consecutive_losses', 'okx_errors', 'volatility_halt', 'agent_budget']
const KINDS = ['entry', 'close', 'partial_close', 'amend_stop', 'amend_targets']

/** First-strong isolate: untrusted text keeps its own direction (dir="auto"). */
const auto = (s: string) => `⁨${s}⁩`

const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '')
const int = (v: unknown): number => {
  const n = Number(v)
  return Number.isFinite(n) ? Math.trunc(n) : 0
}

/** A price at its sent precision, at least 1 decimal above 1,000 and 2 below: "4,180.0", "262.40". */
function price(v: unknown, lang: Lang): string {
  const s = str(v)
  if (!s) return '—'
  return formatDecimal(s, lang, Math.abs(Number(s)) >= 1000 ? 1 : 2)
}

const size = (v: unknown, lang: Lang) => (str(v) ? formatDecimal(str(v), lang, 0) : '—')

export interface NotificationView {
  title: string
  body: string
  meta: string
  action: string
  tone: Tone
  icon: LucideIcon
  critical: boolean
  /** The body is an untrusted quote (a news headline): rendered in a `q` with dir="auto". */
  quote: boolean
}

export function viewOf(n: NotificationRow, t: TFunction<'notifications'>, lang: Lang): NotificationView {
  const p = n.params ?? {}
  const ev = isEventId(n.event) ? EXTRA[n.event] : { icon: Bell, action: 'warroom' }
  const inst = (v: unknown) => iso(str(v))
  const time = (v: unknown) => (str(v) ? iso(formatTime(str(v), lang)) : '—')
  let title = ''
  let body = ''
  let meta = ''
  let quote = false
  let tone: Tone = toneOf(n.event)

  switch (n.event) {
    case 'proposal_awaiting_approval': {
      const kind = KINDS.includes(str(p.kind)) ? str(p.kind) : 'entry'
      title = t('t.proposal')
      body = t(`b.proposal.${kind}`, {
        dir: t(p.side === 'sell' ? 'dir.sell' : 'dir.buy'),
        inst: inst(p.inst_id),
        notional: str(p.final_notional_usd) ? iso(formatMoney(str(p.final_notional_usd), 'USD', lang)) : '—',
        time: time(p.approval_expires_at),
      })
      break
    }
    case 'order_filled':
      title = t('t.filled')
      body = t(p.side === 'sell' ? 'b.filled.sell' : 'b.filled.buy', { sz: iso(size(p.filled_sz, lang)), inst: inst(p.inst_id), px: iso(price(p.avg_px, lang)) })
      break
    case 'stop_hit':
    case 'target_hit':
      title = t(n.event === 'stop_hit' ? 't.stop' : 't.target', { inst: inst(p.inst_id) })
      body = t('b.trigger', { trigger: iso(price(p.trigger_px, lang)), actual: iso(price(p.actual_px, lang)), sz: iso(size(p.sz, lang)) })
      break
    case 'circuit_breaker': {
      title = t('t.breaker')
      const name = str(p.name)
      const why = BREAKERS.includes(name) ? t(`b.breaker.${name}`) : auto(str(p.reason))
      body = `${why} ${t(p.self_clearing === true ? 'b.selfClear' : 'b.resume')}`.trim()
      break
    }
    case 'data_stale':
      title = t('t.stale')
      body = t('b.stale')
      break
    case 'reconciliation_mismatch':
      title = t('t.recon')
      body = t('b.recon')
      break
    case 'kill_switch': {
      title = t(p.flatten === true ? 't.killFlat' : 't.kill')
      const closed = int(p.closed)
      const failed = int(p.failed)
      body = t('b.kill', { cancelled: t('count.cancelled', { count: int(p.cancelled) }), kept: t('count.kept', { count: int(p.stops_kept) }) })
      if (closed) body += ` ${t('b.killClosed', { closed: t('count.closed', { count: closed }) })}`
      if (failed) body += ` ${t('b.killFailed', { failed: t('count.failed', { count: failed }) })}`
      break
    }
    case 'agent_degraded': {
      const slug = str(p.agent)
      const agent = t(`warroom:agents.${slug}.name`, { defaultValue: slug })
      const limited = p.state === 'rate_limited'
      title = t(limited ? 't.agentLimit' : 't.agentDegraded', { agent })
      body = limited && str(p.resets_at) ? t('b.agentLimit', { time: time(p.resets_at) }) : t('b.agentDegraded')
      break
    }
    case 'daily_report':
      title = t('t.daily')
      body = t('b.daily', { day: str(p.day) ? iso(formatDate(`${str(p.day)}T12:00:00Z`, lang)) : '—' })
      break
    case 'news_alert': {
      const ids = Array.isArray(p.inst_ids) ? p.inst_ids.map(str).filter(Boolean) : []
      title =
        ids.length > 1
          ? t('t.newsMore', { inst: inst(ids[0]), n: iso(String(ids.length - 1)) })
          : t('t.news', { inst: inst(ids[0] ?? '') })
      body = str(p.headline)
      quote = true
      meta = t('m.news', { source: auto(str(p.source_name)), n: iso(str(p.importance)) })
      if (str(p.importance) === '5') tone = 'critical'
      break
    }
    default:
      title = t(n.title_key.replace(/^notifications\./, ''), { defaultValue: n.event })
  }

  return { title, body, meta, action: t(`a.${ev.action}`), tone, icon: ev.icon, critical: n.severity === 'critical', quote }
}

/** Plain text for the browser banner: the same words, the headline in quotes. */
export function plainOf(v: NotificationView): { title: string; body: string } {
  const body = v.quote ? `“${auto(v.body)}”` : v.body
  return { title: v.title, body: v.meta ? `${body} ${v.meta}` : body }
}

export const TILE_CLASS: Record<Tone, string> = {
  accent: 'bg-accent-wash text-accent-ink',
  critical: 'bg-loss-wash text-loss-ink',
  warning: 'bg-warning-wash text-warning-ink',
  profit: 'bg-profit-wash text-profit-ink',
  neutral: 'bg-fill-3 text-label-secondary',
}

/** Design v18 §5.5: info 6 s, warning 10 s, critical until closed. */
export function toastDuration(severity: string): number | null {
  if (severity === 'critical') return null
  return severity === 'warning' ? 10_000 : 6_000
}
