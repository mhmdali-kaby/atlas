import { Clock, Info, ListChecks } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import type { Lang } from '../../lib/format'
import { Button, toast } from '../../ui'
import { useNow } from '../portfolio/hooks'
import { AsOf } from './historianParts'
import { fillSlots, slot } from './slots'
import { Inline } from './text'
import { formatTime } from './time'
import {
  apiRows,
  useWatchlistDay,
  useWatchlistWrite,
  useWlConflicts,
  type Pick,
  type WatchlistCard as Card,
  type WriteKind,
  type WriteResult,
} from './watchlist'
import { WatchlistConflict, WatchlistEditBand } from './WatchlistEditBand'
import { titleDate, Time } from './watchlistFormat'
import { WatchlistFoot, WatchlistNotices } from './WatchlistNotices'
import { WatchlistNotPicked } from './WatchlistNotPicked'
import { PickRow, type RowCtx } from './WatchlistPick'
import { WatchlistRail } from './WatchlistRail'
import './watchlist.css'

/*
 * Market Scout's watchlist card (design v9 "Watchlist and news briefs" §A): the day's agenda.
 * It reads what the Scout published from the message's card (ATL-108/109/120) and the day's
 * live state from ATL-103's API: when the card is the current version, its rows are the API's
 * (so the owner's removals and pins show), and while `edit_window_ends_at` is in the future a
 * band offers the countdown, Pin instrument, and Remove/Undo per row. A 409 closes it for good.
 * Notices and reasons are UI strings built from codes; agent and news text is data (dir=auto).
 * This file holds the state, the handlers and the layout; the parts live beside it.
 */

const ROW_LIMIT = 5
const LAST_MINUTE_MS = 60_000
const CAP_DEFAULT = 0.5

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

  // ---- layout -------------------------------------------------------------------------------
  const capValue = card.picks.find((p) => p.convictionBeforeCap !== null)?.conviction ?? CAP_DEFAULT
  const windowOver = endsMs !== null && now >= endsMs
  const showFoot = !open && (windowOver || Boolean(conflict))
  const drainFrom = endsMs !== null && card.createdAt ? Math.max(0, Math.min(1, left / (endsMs - Date.parse(card.createdAt) || 1))).toFixed(4) : 1

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
        <WatchlistConflict conflict={conflict} />
      ) : open ? (
        <WatchlistEditBand
          endsAt={endsAt!}
          left={left}
          drainFrom={drainFrom}
          lang={lang}
          pinRef={pinBtn}
          popOpen={popOpen}
          onTogglePop={() => setPopOpen((v) => !v)}
          onClosePop={() => setPopOpen(false)}
          env={env}
          candidates={card.rejected.map((r) => ({ instId: r.instId, assetClass: assetOf(r.instId) }))}
          onList={onList}
          onPin={(inst) => pin(inst, true)}
        />
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

      <WatchlistNotices card={card} capValue={capValue} lang={lang} onJump={onJump} />

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
        <WatchlistRail date={card.date} rows={live} trigger={card.trigger} now={now} lang={lang} linkedInst={hoverRow} onHoverCatalyst={setHoverCat} />
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

      <WatchlistNotPicked rejected={card.rejected} live={live} onList={onList} open={open} busy={busy} onPin={(inst) => void pin(inst, false)} />

      {showFoot && (
        <WatchlistFoot card={card} rows={rows} live={live} updated={updated} windowOver={windowOver} conflict={Boolean(conflict)} endsAt={endsAt} lang={lang} />
      )}

      <p className="sr-only" aria-live="polite" data-testid="wl-live">
        {announce}
      </p>
    </div>
  )
}
