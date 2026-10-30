import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  CircleMinus,
  Clock,
  Info,
  ListChecks,
  Lock,
  Pin,
  SquarePen,
  TriangleAlert,
  Undo2,
  Zap,
} from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { DATE_LOCALE, formatFixed, type Lang } from '../../lib/format'
import { Button, Spinner, toast } from '../../ui'
import { useNow } from '../portfolio/hooks'
import { AsOf, Fig } from './historianParts'
import { PinPopover } from './PinPopover'
import { fillSlots, slot } from './slots'
import { Inline, InstrumentId } from './text'
import { baghdadDay, formatTime } from './time'
import {
  apiRows,
  isDerivative,
  mmss,
  useWatchlistDay,
  useWatchlistWrite,
  useWlConflicts,
  type Bias,
  type Pick,
  type WatchlistCard as Card,
  type WriteKind,
  type WriteResult,
} from './watchlist'
import { WatchlistRail } from './WatchlistRail'
import './watchlist.css'

/*
 * Market Scout's watchlist card (design v9 "Watchlist and news briefs" §A): the day's agenda.
 * It reads what the Scout published from the message's card (ATL-108/109/120) and the day's
 * live state from ATL-103's API: when the card is the current version, its rows are the API's
 * (so the owner's removals and pins show), and while `edit_window_ends_at` is in the future a
 * band offers the countdown, Pin instrument, and Remove/Undo per row. A 409 closes it for good.
 * Notices and reasons are UI strings built from codes; agent and news text is data (dir=auto).
 */

const ROW_LIMIT = 5
const LAST_MINUTE_MS = 60_000
const CAP_DEFAULT = 0.5

const css = (vars: Record<string, string | number>) => vars as CSSProperties

/** "A, B and C" with React nodes, in the reader's language. */
function joinList(items: ReactNode[], lang: Lang): ReactNode[] {
  const marks = items.map((_, i) => `\u0001${i}\u0001`)
  const parts = new Intl.ListFormat(lang === 'ar' ? 'ar' : 'en', { style: 'long', type: 'conjunction' }).formatToParts(marks)
  return parts.map((p, k) => <Fragment key={k}>{p.type === 'element' ? items[Number(p.value.slice(1, -1))] : p.value}</Fragment>)
}
const insts = (ids: string[], lang: Lang) => joinList(ids.map((id) => <InstrumentId key={id} id={id} />), lang)

/** "Wednesday 28 October" for a "YYYY-MM-DD" day: formatted at midday Baghdad, never at midnight. */
function titleDate(date: string, lang: Lang): string {
  return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, { timeZone: 'Asia/Baghdad', weekday: 'long', day: 'numeric', month: 'long' }).format(
    new Date(`${date}T09:00:00Z`),
  )
}

const conv = (v: number, lang: Lang) => formatFixed(v, 2, lang)
const price = (v: number, lang: Lang) => formatFixed(v, Math.abs(v) >= 1000 && Number.isInteger(v) ? 0 : 2, lang)

function Time({ ts, lang }: { ts: string | number; lang: Lang }) {
  const iso = typeof ts === 'number' ? new Date(ts).toISOString() : ts
  return (
    <time className="fig num" dateTime={iso}>
      {formatTime(ts, lang)}
    </time>
  )
}

// ------------------------------------------------------------------------------------- parts

const BIAS_ICON: Record<Bias, typeof ArrowUpRight> = { long: ArrowUpRight, short: ArrowDownRight, neutral: ArrowRight }

function BiasLabel({ bias }: { bias: Bias }) {
  const { t } = useTranslation('warroom')
  const Icon = BIAS_ICON[bias]
  return (
    <span className={`wl-bias is-${bias}`} data-testid="wl-bias">
      <span className="sr-only">{t('wl.biasSr')}: </span>
      <Icon aria-hidden="true" strokeWidth={2} className="icon-dir" data-testid={`wl-bias-icon-${bias}`} />
      <span>{t(`wl.bias.${bias}`)}</span>
    </span>
  )
}

function Conviction({ pick: p, card, lang }: { pick: Pick; card: Card; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const capped = p.convictionBeforeCap !== null
  let tip = ''
  if (capped) {
    const vars = { cap: conv(p.conviction, lang), before: conv(p.convictionBeforeCap!, lang) }
    if (p.capReason === 'news_class_down') {
      const since = card.degradedSince[p.assetClass]
      const cls = t(`wl.dcls.${p.assetClass}`, { defaultValue: t('wl.dcls.other') })
      tip = since ? t('wl.convCapTip.news_class_down', { ...vars, cls, time: formatTime(since, lang) }) : t('wl.convCapTip.news_class_down_open', { ...vars, cls })
    } else {
      tip = t('wl.convCapTip.partial_evidence', vars)
    }
  }
  const label = t('wl.convSr', { v: conv(p.conviction, lang) }) + (capped ? `. ${tip}` : '')
  return (
    <span
      className={`wl-conv${capped ? ' is-capped' : ''}`}
      role="img"
      aria-label={label}
      title={capped ? tip : undefined}
      tabIndex={capped ? 0 : undefined}
      data-testid="wl-conv"
      data-capped={capped || undefined}
    >
      <Fig>{conv(p.conviction, lang)}</Fig>
      <span className="wl-meter" aria-hidden="true">
        <i style={css({ '--v': p.conviction })} />
        {capped && <b style={css({ '--v': p.conviction })} />}
      </span>
    </span>
  )
}

function Chips({ pick: p, date, now, lang }: { pick: Pick; date: string; now: number; lang: Lang }) {
  const { t } = useTranslation('warroom')
  if (p.catalysts.length === 0) return null
  const weekday = (ms: number) => new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, { timeZone: 'Asia/Baghdad', weekday: 'short' }).format(new Date(ms))
  return (
    <ul className="wl-chips" data-testid="wl-chips">
      {p.catalysts.map((k) => {
        const ms = Date.parse(k.time)
        const otherDay = baghdadDay(ms) !== date
        return (
          <li
            key={`${k.time}|${k.name}`}
            className={`wl-chip${ms < now ? ' is-past' : ''}${k.importance >= 5 ? ' is-imp5' : ''}`}
            title={t('wl.rail.tip', { time: formatTime(ms, lang), name: k.name, imp: k.importance, insts: p.instId })}
            data-testid="wl-chip"
          >
            <i aria-hidden="true" />
            {otherDay && <span>{weekday(ms)}</span>}
            <b>
              <Time ts={ms} lang={lang} />
            </b>
            <span dir="auto">{k.name}</span>
          </li>
        )
      })}
    </ul>
  )
}

function Levels({ pick: p, lang }: { pick: Pick; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const segs: Array<[string, number]> = []
  if (p.levels.support !== null) segs.push(['support', p.levels.support])
  if (p.levels.resistance !== null) segs.push(['resistance', p.levels.resistance])
  if (!p.invalidation && p.levels.invalidation !== null) segs.push(['invalidation', p.levels.invalidation])
  return (
    <>
      {segs.length > 0 && (
        <p className="wl-levels" data-testid="wl-levels">
          {segs.map(([k, v]) => (
            <span key={k} className="wl-seg">
              <span>{t(`wl.${k}`)}</span> <Fig>{price(v, lang)}</Fig>
            </span>
          ))}
        </p>
      )}
      {p.invalidation && (
        <p className="wl-inval" data-testid="wl-inval">
          <span className="wl-inval-k">{t('wl.invalidation')}</span>
          <span dir="auto">
            <Inline text={p.invalidation} />
          </span>
        </p>
      )}
    </>
  )
}

// ------------------------------------------------------------------------------------- rows

interface RowCtx {
  card: Card
  open: boolean
  now: number
  lang: Lang
  busy: Record<string, boolean>
  linkedInsts: string[] | null
  isNew: (inst: string) => boolean
  onRemove: (p: Pick) => void
  onUndo: (p: Pick) => void
  onHoverRow: (inst: string | null) => void
}

function PickRow({ pick: p, ctx }: { pick: Pick; ctx: RowCtx }) {
  const { t } = useTranslation('warroom')
  const { card, open, lang, busy } = ctx
  const clsWord = (() => {
    const w = t(`wl.cls.${p.assetClass}`, { defaultValue: t('wl.cls.other') })
    return isDerivative(p.instId) ? w : t('wl.spotOf', { cls: w })
  })()
  const actions = !open ? null : busy[p.instId] ? (
    <span className="wl-busy">
      <Spinner />
    </span>
  ) : p.removedAt ? (
    <Button
      variant="plain"
      size="sm"
      className="wl-undo"
      icon={<Undo2 aria-hidden="true" strokeWidth={1.5} className="icon-dir" />}
      aria-label={t('wl.undoAria', { inst: p.instId })}
      onClick={() => ctx.onUndo(p)}
      data-wl-action="undo"
      data-testid="wl-undo"
    >
      {t('wl.undo')}
    </Button>
  ) : (
    <Button
      variant="plain"
      size="sm"
      className="wl-rm"
      icon={<CircleMinus aria-hidden="true" strokeWidth={1.5} />}
      aria-label={t('wl.removeAria', { inst: p.instId })}
      onClick={() => ctx.onRemove(p)}
      data-wl-action="remove"
      data-testid="wl-remove"
    >
      {t('wl.remove')}
    </Button>
  )
  const rank = (
    <span className="wl-rank" aria-hidden="true">
      {p.placeholder ? <Pin strokeWidth={1.5} /> : <span className="num">{p.rank}</span>}
    </span>
  )

  if (p.removedAt) {
    return (
      <li className="wl-pick is-removed" data-wl-row={p.instId} data-testid="wl-removed">
        {rank}
        <div className="wl-body">
          <p className="wl-l1">
            <span className="wl-inst">
              <InstrumentId id={p.instId} />
            </span>
            <span className="wl-rec">{fillSlots(t('wl.removedBy', { time: slot('time') }), { time: <Time ts={p.removedAt} lang={lang} /> })}</span>
          </p>
        </div>
        <div className="wl-act">{actions}</div>
      </li>
    )
  }

  const linked = ctx.linkedInsts?.includes(p.instId) ?? false
  const isNew = ctx.isNew(p.instId)
  return (
    <li
      className={`wl-pick${linked ? ' is-linked' : ''}${p.placeholder ? ' is-pin-only' : ''}`}
      data-wl-row={p.instId}
      data-testid="wl-pick"
      onMouseEnter={() => ctx.onHoverRow(p.instId)}
      onMouseLeave={() => ctx.onHoverRow(null)}
    >
      {rank}
      <div className="wl-body">
        <div className="wl-l1">
          <p className="wl-name">
            <span className="wl-inst" data-testid="wl-inst">
              <InstrumentId id={p.instId} />
            </span>
            <span className="wl-class" data-testid="wl-class">
              {clsWord}
            </span>
            {isNew && <span className="wl-badge wl-new">{t('wl.new')}</span>}
          </p>
          {!p.placeholder && (
            <span className="wl-call">
              <BiasLabel bias={p.bias} />
              <Conviction pick={p} card={card} lang={lang} />
            </span>
          )}
        </div>
        {p.pinned && (
          <p className="wl-pinned" data-testid="wl-pinned">
            {!p.placeholder && <Pin aria-hidden="true" strokeWidth={1.5} />}
            <span>{t('wl.pinnedBy')}</span>
          </p>
        )}
        {p.placeholder ? (
          <p className="wl-pin-note">{t('wl.pinnedNote')}</p>
        ) : (
          <>
            {p.thesis && (
              <p className="wl-thesis" dir="auto" data-testid="wl-thesis">
                <Inline text={p.thesis} />
              </p>
            )}
            <Chips pick={p} date={card.date} now={ctx.now} lang={lang} />
            <Levels pick={p} lang={lang} />
          </>
        )}
      </div>
      <div className="wl-act">{actions}</div>
    </li>
  )
}

// ------------------------------------------------------------------------------------- the card

export interface WatchlistCardProps {
  card: Card
  lang: Lang
  onJump: (messageId: string) => void
}

export function WatchlistCard({ card, lang, onJump }: WatchlistCardProps) {
  const { t } = useTranslation('warroom')
  const env = useTradingEnv()
  const day = useWatchlistDay(env, card.date)
  const write = useWatchlistWrite(env, card.date)
  const apiW = day.data?.watchlist ?? null
  // The API's watchlist when this card is the day's current version; otherwise the newer one.
  const cur = apiW && apiW.id === card.watchlistId ? apiW : null
  const newer = apiW && apiW.id !== card.watchlistId && apiW.version > card.version ? apiW : null
  const conflict = useWlConflicts((s) => s.byId[card.watchlistId])
  const setConflict = useWlConflicts((s) => s.set)

  const endsAt = cur?.edit_window_ends_at ?? card.editEndsAt
  const endsMs = endsAt ? Date.parse(endsAt) : null
  const canOpen = Boolean(cur?.edit_window_open) && !conflict && endsMs !== null
  const now = useNow(canOpen ? 250 : 30_000)
  const open = canOpen && now < endsMs!
  const left = endsMs !== null ? endsMs - now : 0

  // Optimistic removals/restores (inst → removedAt or null) and rows waiting for the server.
  const [optimistic, setOptimistic] = useState<Record<string, string | null>>({})
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [popOpen, setPopOpen] = useState(false)
  const [more, setMore] = useState(false)
  const [hoverRow, setHoverRow] = useState<string | null>(null)
  const [hoverCat, setHoverCat] = useState<string[] | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const pinBtn = useRef<HTMLButtonElement>(null)
  const pendingFocus = useRef<{ inst: string; action: 'undo' | 'remove' } | 'pin' | 'conflict' | null>(null)

  const rows = useMemo(() => {
    const cardBy = new Map(card.picks.map((p) => [p.instId, p]))
    const base = cur
      ? apiRows(cur).map((r) => {
          const c = cardBy.get(r.instId)
          // The API keeps no cap metadata: take it from the card when the value is the same.
          return c && c.convictionBeforeCap !== null && Math.abs(c.conviction - r.conviction) < 1e-6
            ? { ...r, convictionBeforeCap: c.convictionBeforeCap, capReason: c.capReason }
            : r
        })
      : card.picks
    const withLocal = base.map((p) => (p.instId in optimistic ? { ...p, removedAt: optimistic[p.instId] ?? null } : p))
    return [...withLocal.filter((p) => !p.placeholder), ...withLocal.filter((p) => p.placeholder)]
  }, [card.picks, cur, optimistic])
  const live = rows.filter((r) => !r.removedAt)
  const onList = useMemo(() => new Set(live.map((r) => r.instId)), [live])
  const empty = card.status === 'empty' && rows.length === 0
  const updated = card.version > 1

  // ---- live announcements: once at one minute left, once at close (the countdown is not live) --
  const [sawOpen, setSawOpen] = useState(false)
  if (open && !sawOpen) setSawOpen(true)
  const announce = open ? (left <= LAST_MINUTE_MS ? t('wl.oneMinuteLive') : '') : sawOpen && !conflict ? t('wl.closedLive') : ''

  // ---- focus after an action (design §A13) ----------------------------------------------------
  useEffect(() => {
    const f = pendingFocus.current
    if (!f || !root.current) return
    let el: HTMLElement | null = null
    if (f === 'pin') el = pinBtn.current
    else if (f === 'conflict') el = root.current.querySelector<HTMLElement>('[data-testid="wl-conflict"]')
    else el = root.current.querySelector<HTMLElement>(`[data-wl-row="${CSS.escape(f.inst)}"] [data-wl-action="${f.action}"]`)
    if (el) {
      el.focus({ preventScroll: true })
      pendingFocus.current = null
    }
  })

  const failToast = (kind: WriteKind, inst: string) =>
    toast({
      tone: 'error',
      title: t(kind === 'remove' ? 'wl.toast.removeFail' : kind === 'restore' ? 'wl.toast.restoreFail' : 'wl.toast.pinFail', { inst }),
      body: t('wl.toast.failBody'),
    })

  const onConflict = useCallback(
    (kind: WriteKind, instId: string, r: WriteResult) => {
      if (r.ok || r.status !== 409 || (r.error !== 'edit_window_closed' && r.error !== 'not_current_version')) return false
      setConflict(card.watchlistId, { kind, instId, error: r.error })
      setPopOpen(false)
      pendingFocus.current = 'conflict'
      return true
    },
    [card.watchlistId, setConflict],
  )

  const edit = async (kind: 'remove' | 'restore', p: Pick) => {
    if (!p.itemId) return
    setOptimistic((o) => ({ ...o, [p.instId]: kind === 'remove' ? new Date().toISOString() : null }))
    setBusy((b) => ({ ...b, [p.instId]: true }))
    const r = await write(kind, card.watchlistId, { itemId: p.itemId })
    setBusy((b) => ({ ...b, [p.instId]: false }))
    setOptimistic((o) => {
      const next = { ...o }
      delete next[p.instId]
      return next
    })
    if (r.ok) {
      pendingFocus.current = { inst: p.instId, action: kind === 'remove' ? 'undo' : 'remove' }
      if (kind === 'remove') toast({ title: t('wl.toast.removed', { inst: p.instId }), body: endsAt ? t('wl.toast.removedBody', { time: formatTime(endsAt, lang) }) : undefined })
      else toast({ title: t('wl.toast.restored', { inst: p.instId }) })
      return
    }
    if (!onConflict(kind, p.instId, r)) failToast(kind, p.instId)
  }

  const pin = async (instId: string, fromPopover: boolean): Promise<WriteResult> => {
    if (!fromPopover) setBusy((b) => ({ ...b, [instId]: true }))
    const r = await write('pin', card.watchlistId, { instId })
    if (!fromPopover) setBusy((b) => ({ ...b, [instId]: false }))
    if (r.ok) {
      if (fromPopover) {
        setPopOpen(false)
        pendingFocus.current = 'pin'
      }
      toast({ title: t('wl.toast.pinned', { inst: instId }), body: t('wl.toast.pinnedBody') })
    } else if (!onConflict('pin', instId, r) && !fromPopover) {
      failToast('pin', instId)
    }
    return r
  }

  // ---- pieces -------------------------------------------------------------------------------
  const capValue = card.picks.find((p) => p.convictionBeforeCap !== null)?.conviction ?? CAP_DEFAULT
  const capFig = <Fig>{conv(capValue, lang)}</Fig>
  const agentName = (slug: string) => t(`agents.${slug}.name`, { defaultValue: slug })

  const notices: ReactNode[] = []
  if (card.partial) {
    const why = [
      ...card.partial.teammates.map((tm) => t(`wl.partialWhy.${tm.status}`, { agent: agentName(tm.agent) })),
      ...(card.partial.staleMinutes !== null ? [t('wl.partialWhy.stale', { min: t('wl.min', { count: card.partial.staleMinutes }) })] : []),
    ]
    const text = why.length
      ? fillSlots(t('wl.partial', { why: slot('why'), cap: slot('cap') }), { why: <>{joinList(why, lang)}</>, cap: capFig })
      : fillSlots(t('wl.partialBare', { cap: slot('cap') }), { cap: capFig })
    notices.push(
      <p key="partial" className="wl-note is-warn" data-testid="wl-partial">
        <TriangleAlert aria-hidden="true" strokeWidth={1.5} />
        <span>{text}</span>
      </p>,
    )
  }
  for (const cls of card.degradedClasses) {
    const since = card.degradedSince[cls] ?? null
    const word = t(`wl.dcls.${cls}`, { defaultValue: cls })
    notices.push(
      <p key={`deg-${cls}`} className="wl-note is-warn" data-testid="wl-degraded">
        <TriangleAlert aria-hidden="true" strokeWidth={1.5} />
        <span>
          {since
            ? fillSlots(t('wl.degradedSince', { cls: word, time: slot('time'), cap: slot('cap') }), { time: <Time ts={since} lang={lang} />, cap: capFig })
            : fillSlots(t('wl.degraded', { cls: word, cap: slot('cap') }), { cap: capFig })}
        </span>
      </p>,
    )
  }
  if (card.trigger) {
    const tr = card.trigger
    const body =
      tr.kind === 'news_alert' ? (
        <>
          <span>{fillSlots(t('wl.trigger.news_alert', { imp: slot('imp'), time: slot('time') }), { imp: <Fig>{tr.importance}</Fig>, time: <Time ts={tr.at} lang={lang} /> })}</span>
          {tr.headline && (
            <span className="wl-head-q" dir="auto">
              {tr.headline}
            </span>
          )}
        </>
      ) : (
        <span>
          {fillSlots(t('wl.trigger.vol_spike', { sigma: slot('sigma'), inst: slot('inst'), time: slot('time') }), {
            sigma: <Fig>{`${formatFixed(tr.sigma, 1, lang)}σ`}</Fig>,
            inst: <InstrumentId id={tr.instId} />,
            time: <Time ts={tr.at} lang={lang} />,
          })}
        </span>
      )
    const Icon = tr.kind === 'news_alert' ? Zap : Activity
    const messageId = tr.kind === 'news_alert' ? tr.messageId : null
    notices.push(
      messageId ? (
        <a
          key="trigger"
          className="wl-trigger"
          href={`#${messageId}`}
          onClick={(e) => {
            e.preventDefault()
            onJump(messageId)
          }}
          data-testid="wl-trigger"
        >
          <Icon aria-hidden="true" strokeWidth={1.5} />
          <span className="wl-trig-b">{body}</span>
        </a>
      ) : (
        <p key="trigger" className="wl-trigger" data-testid="wl-trigger">
          <Icon aria-hidden="true" strokeWidth={1.5} />
          <span className="wl-trig-b">{body}</span>
        </p>
      ),
    )
  }
  if (card.changes) {
    const since = card.changes.since ?? card.createdAt
    const time = since ? <Time ts={since} lang={lang} /> : null
    if (card.changes.added.length)
      notices.push(
        <p key="added" className="wl-change" data-testid="wl-added">
          {fillSlots(t('wl.changes.added', { time: slot('time'), list: slot('list') }), { time, list: <>{insts(card.changes.added, lang)}</> })}
        </p>,
      )
    if (card.changes.dropped.length)
      notices.push(
        <p key="dropped" className="wl-change" data-testid="wl-dropped">
          {fillSlots(t('wl.changes.dropped', { time: slot('time'), list: slot('list') }), { time, list: <>{insts(card.changes.dropped, lang)}</> })}
        </p>,
      )
  }

  // The owner's edits, for the footer once the window is over.
  const cardPinned = new Set(card.picks.filter((p) => p.pinned).map((p) => p.instId))
  const removed = rows.filter((r) => r.removedAt).map((r) => r.instId)
  const pinnedNow = live.filter((r) => r.pinned && !cardPinned.has(r.instId)).map((r) => r.instId)
  const carried = updated ? live.filter((r) => r.pinned && cardPinned.has(r.instId)).map((r) => r.instId) : []
  const edits: ReactNode[] = []
  if (removed.length && pinnedNow.length)
    edits.push(fillSlots(t('wl.edits.both', { removed: slot('removed'), pinned: slot('pinned') }), { removed: <>{insts(removed, lang)}</>, pinned: <>{insts(pinnedNow, lang)}</> }))
  else if (removed.length) edits.push(fillSlots(t('wl.edits.removed', { list: slot('list') }), { list: <>{insts(removed, lang)}</> }))
  else if (pinnedNow.length) edits.push(fillSlots(t('wl.edits.pinned', { list: slot('list') }), { list: <>{insts(pinnedNow, lang)}</> }))
  if (carried.length) edits.push(fillSlots(t('wl.edits.carried', { list: slot('list') }), { list: <>{insts(carried, lang)}</> }))
  const windowOver = endsMs !== null && now >= endsMs
  const showFoot = !open && (windowOver || Boolean(conflict))

  const shown = rows.length > ROW_LIMIT + 1 && !more ? rows.slice(0, ROW_LIMIT) : rows
  const rowCtx: RowCtx = {
    card,
    open,
    now,
    lang,
    busy,
    linkedInsts: hoverCat,
    isNew: (inst) => card.changes?.added.includes(inst) ?? false,
    onRemove: (p) => void edit('remove', p),
    onUndo: (p) => void edit('restore', p),
    onHoverRow: setHoverRow,
  }
  const assetOf = (instId: string) => rows.find((r) => r.instId === instId)?.assetClass ?? 'other'

  return (
    <div ref={root} className={`wl-card${open ? ' is-open' : ''}${empty ? ' is-empty' : ''}`} role="group" aria-label={t('kind.watchlist')} data-testid="wl-card" data-open={open || undefined}>
      <div className="hv-head wl-head">
        <span className="hv-tile">
          <ListChecks aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{t('kind.watchlist')}</span>
        {updated && <span className="wl-badge wl-upd">{t('wl.updated')}</span>}
        {card.dataAsOf && <AsOf ts={card.dataAsOf} lang={lang} />}
      </div>

      {newer && (
        <p className="wl-replaced" data-testid="wl-replaced">
          <Info aria-hidden="true" strokeWidth={1.5} />
          <span>{fillSlots(t('wl.replaced', { time: slot('time') }), { time: <Time ts={newer.created_at} lang={lang} /> })}</span>
          {newer.message_id && (
            <a
              className="wl-link"
              href={`#${newer.message_id}`}
              onClick={(e) => {
                e.preventDefault()
                onJump(newer.message_id!)
              }}
            >
              {t('wl.showUpdate')}
            </a>
          )}
        </p>
      )}

      {conflict ? (
        <div className="wl-conflict" role="alert" tabIndex={-1} data-testid="wl-conflict">
          <TriangleAlert aria-hidden="true" strokeWidth={1.5} />
          <div>
            <p className="wl-conflict-t">{t(conflict.error === 'not_current_version' ? 'wl.replacedTitle' : 'wl.conflictTitle')}</p>
            <p>
              {fillSlots(t(`${conflict.error === 'not_current_version' ? 'wl.replacedConflict' : 'wl.conflict'}.${conflict.kind}`, { inst: slot('inst') }), {
                inst: <InstrumentId id={conflict.instId} />,
              })}
            </p>
          </div>
        </div>
      ) : open ? (
        <div className="wl-band-wrap">
          <div className={`wl-band${left <= LAST_MINUTE_MS ? ' is-last' : ''}`} data-testid="wl-band">
            <span className="wl-band-ic">
              <SquarePen aria-hidden="true" strokeWidth={1.5} />
            </span>
            <p className="wl-band-t">{fillSlots(t('wl.window', { time: slot('time') }), { time: <Time ts={endsAt!} lang={lang} /> })}</p>
            <span className="wl-left" data-testid="wl-countdown">
              {fillSlots(t('wl.left', { t: slot('t') }), { t: <Fig>{mmss(left)}</Fig> })}
            </span>
            <Button
              ref={pinBtn}
              size="sm"
              variant="secondary"
              className="wl-pin-btn"
              icon={<Pin aria-hidden="true" strokeWidth={1.5} />}
              aria-haspopup="dialog"
              aria-expanded={popOpen}
              onClick={() => setPopOpen((v) => !v)}
              data-wl-pin-trigger=""
              data-testid="wl-pin-instrument"
            >
              {t('wl.pinBtn')}
            </Button>
            <span
              className="wl-drain"
              aria-hidden="true"
              style={css({
                '--from': endsMs !== null && card.createdAt ? Math.max(0, Math.min(1, left / (endsMs - Date.parse(card.createdAt) || 1))).toFixed(4) : 1,
                animationDuration: `${Math.max(0, left)}ms`,
              })}
            />
          </div>
          {popOpen && (
            <PinPopover
              env={env}
              candidates={card.rejected.map((r) => ({ instId: r.instId, assetClass: assetOf(r.instId) }))}
              onList={onList}
              onPin={(inst) => pin(inst, true)}
              onClose={(back) => {
                setPopOpen(false)
                if (back) pinBtn.current?.focus()
              }}
            />
          )}
        </div>
      ) : null}

      <div className="wl-titles">
        <p className="hv-q wl-title" dir="auto" data-testid="wl-title">
          {empty
            ? t('wl.titleEmpty')
            : updated && card.createdAt
              ? fillSlots(t('wl.titleUpdated', { time: slot('time') }), { time: <Time ts={card.createdAt} lang={lang} /> })
              : titleDate(card.date, lang)}
        </p>
        {!empty && card.shortlistCount !== null && card.shortlistCount > 0 && (
          <p className="wl-funnel" data-testid="wl-funnel">
            {t('wl.funnel', { picks: t('wl.picks', { count: card.picks.length }), short: t('wl.inst', { count: card.shortlistCount }) })}
          </p>
        )}
        {empty && (
          <>
            <p className="wl-reason" data-testid="wl-empty-reason">
              {card.emptyShortlist || card.shortlistCount === null
                ? t('wl.empty.empty_shortlist')
                : t('wl.empty.no_picks', { short: t('wl.inst', { count: card.shortlistCount }) })}
            </p>
            <p className="wl-next">
              <Clock aria-hidden="true" strokeWidth={1.5} />
              <span>{t('wl.empty.next')}</span>
            </p>
          </>
        )}
      </div>

      {notices}

      {card.backdrop.length > 0 && (
        <section className="wl-back" aria-label={t('wl.backdrop')} data-testid="wl-backdrop">
          <h4 className="wl-sec">{t('wl.backdrop')}</h4>
          {card.backdrop.map((line, i) => (
            <p key={i} dir="auto">
              <Inline text={line} />
            </p>
          ))}
        </section>
      )}

      {live.length > 0 && (
        <WatchlistRail
          date={card.date}
          rows={live}
          trigger={card.trigger}
          now={now}
          lang={lang}
          linkedInst={hoverRow}
          onHoverCatalyst={setHoverCat}
        />
      )}

      {rows.length > 0 && (
        <>
          <ol className="wl-list" data-testid="wl-list">
            {shown.map((p) => (
              <PickRow key={p.instId} pick={p} ctx={rowCtx} />
            ))}
          </ol>
          {rows.length > ROW_LIMIT + 1 && (
            <Button variant="plain" size="sm" className="wl-more" aria-expanded={more} onClick={() => setMore((v) => !v)}>
              {more ? t('wl.fewer') : t('wl.more', { count: rows.length - ROW_LIMIT })}
            </Button>
          )}
        </>
      )}

      {card.rejected.length > 0 && (
        <section className="wl-rej" aria-label={t('wl.notPicked')} data-testid="wl-rejected">
          <h4 className="wl-sec">{t('wl.notPicked')}</h4>
          <ul>
            {card.rejected.map((r) => {
              const pinned = live.some((x) => x.instId === r.instId && x.pinned)
              let act: ReactNode = null
              if (pinned)
                act = (
                  <span className="wl-pinned-s">
                    <Pin aria-hidden="true" strokeWidth={1.5} />
                    <span>{t('wl.pinnedBy')}</span>
                  </span>
                )
              else if (open && !onList.has(r.instId))
                act = busy[r.instId] ? (
                  <span className="wl-busy">
                    <Spinner />
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="wl-pin-one"
                    icon={<Pin aria-hidden="true" strokeWidth={1.5} />}
                    aria-label={t('wl.pinAria', { inst: r.instId })}
                    onClick={() => void pin(r.instId, false)}
                    data-testid="wl-pin-rejected"
                  >
                    {t('wl.pin')}
                  </Button>
                )
              return (
                <li key={r.instId} data-testid="wl-rejected-row">
                  <span className="wl-inst">
                    <InstrumentId id={r.instId} />
                  </span>
                  <span className="wl-why" dir="auto">
                    <Inline text={r.reason} />
                  </span>
                  <span className="wl-ract">{act}</span>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {showFoot && (windowOver || edits.length > 0) && (
        <p className="wl-foot" data-testid="wl-foot">
          <Lock aria-hidden="true" strokeWidth={1.5} />
          <span>
            {windowOver && !conflict && endsAt && fillSlots(t('wl.closed', { time: slot('time') }), { time: <Time ts={endsAt} lang={lang} /> })}
            {edits.map((e, i) => (
              <Fragment key={i}>
                {(i > 0 || (windowOver && !conflict)) && ' '}
                {e}
              </Fragment>
            ))}
          </span>
        </p>
      )}

      <p className="sr-only" aria-live="polite" data-testid="wl-live">
        {announce}
      </p>
    </div>
  )
}
