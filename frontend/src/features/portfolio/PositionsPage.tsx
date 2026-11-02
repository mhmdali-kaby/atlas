import { Layers, Unplug } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatDecimal, formatPrice, NO_VALUE, type Lang } from '../../lib/format'
import { isVerified, Notice, useConnection } from '../onboarding'
import { Button, SignedValue, toast } from '../../ui'
import { ClosePositionSheet } from './ClosePositionSheet'
import { POLL_MS, usePositions, useSummary, type Position } from './api'
import { useLang, useNow } from './hooks'
import { historyPathForInst } from './historyApi'
import { InstFilterChip, useInstFilterParam } from './instFilter'
import { Duration, InstId, ReconciledIndicator, SideBadge, Skeleton, StateBlock } from './parts'
import { formatDay, formatDayTime } from './time'
import './portfolio.css'

/*
 * Portfolio → Positions (brief §13, design: Positions). Every open position of the newest
 * synced positions tick, from /api/portfolio/positions (polled every 30 s). Stop, targets and
 * the opening agent/proposal arrive with the Strategist (Phase 2); Close (ATL-151) goes
 * through the Executor via ATL-148's POST /api/portfolio/positions/{id}/close.
 */

const COLUMNS = ['instrument', 'side', 'size', 'leverage', 'mgnMode', 'entry', 'mark', 'liq', 'upl', 'time'] as const
type Column = (typeof COLUMNS)[number]
const NUMERIC = new Set<Column>(['size', 'leverage', 'entry', 'mark', 'liq', 'upl', 'time'])

/** A fresh `n` per opening remounts the sheet clean; `open: false` keeps its position while it
 * animates out (the settings-data-sources key sheets use the same pattern). */
type CloseSheetState = { position: Position; open: boolean; n: number } | null

function PositionRow({
  p,
  lang,
  now,
  canClose,
  onClose,
}: {
  p: Position
  lang: Lang
  now: number
  canClose: boolean
  onClose: (p: Position) => void
}) {
  const { t } = useTranslation('portfolio')
  const mode = p.margin_mode === 'isolated' || p.margin_mode === 'cross' ? t(`positions.${p.margin_mode}`) : (p.margin_mode ?? NO_VALUE)
  return (
    <tr data-testid="position-row" data-inst={p.inst_id}>
      <td data-col="instrument">
        <InstId id={p.inst_id} />
      </td>
      <td data-col="side">
        <SideBadge side={p.side} label={t(`positions.${p.side}`)} />
      </td>
      <td data-col="size" className="is-num">
        <span className="fig">{formatDecimal(p.size, lang, 0)}</span>
        <span className="pf-cell-sub">{t('positions.contractsUnit')}</span>
      </td>
      <td data-col="leverage" className="is-num">
        <span className="fig">{p.leverage === null ? NO_VALUE : `${formatDecimal(p.leverage, lang, 0)}×`}</span>
      </td>
      <td data-col="mgnMode">{mode}</td>
      <td data-col="entry" className="is-num fig">
        {formatPrice(p.entry, lang)}
      </td>
      <td data-col="mark" className="is-num fig">
        {formatPrice(p.mark, lang)}
      </td>
      <td data-col="liq" className="is-num fig text-label-secondary">
        {formatPrice(p.liq_px, lang)}
      </td>
      <td data-col="upl" className="is-num">
        <span className="flex justify-end">
          <SignedValue value={p.upl_usd} unit="USD" />
        </span>
        <span className="pf-cell-sub flex justify-end [&_svg]:size-3">
          <SignedValue value={p.upl_pct} unit="percent" />
        </span>
      </td>
      <td data-col="time" className="is-num">
        {p.opened_at ? (
          <>
            <Duration
              ms={now - Date.parse(p.opened_at)}
              lang={lang}
              title={t('positions.opened', { date: formatDayTime(p.opened_at, lang) })}
            />
            <span className="pf-cell-sub">{formatDay(p.opened_at, lang)}</span>
          </>
        ) : (
          <span className="text-label-secondary">{NO_VALUE}</span>
        )}
      </td>
      {canClose && (
        <td data-col="actions">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onClose(p)}
            aria-label={t(`positions.close.aria.${p.side === 'short' ? 'short' : 'long'}`, { inst: p.inst_id })}
          >
            {t('positions.close.btn')}
          </Button>
        </td>
      )}
    </tr>
  )
}

function TableSkeleton() {
  return (
    <div className="pf-panel" data-testid="positions-loading">
      <table className="pf-table pf-positions">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th key={c}>
                <Skeleton style={{ width: 60, height: 12 }} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[0, 1, 2, 3].map((r) => (
            <tr key={r}>
              {COLUMNS.map((c, i) => (
                <td key={c}>
                  <Skeleton style={{ width: i ? 64 : 150, height: i ? 14 : 28 }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PositionsPage() {
  const { t } = useTranslation('portfolio')
  const lang = useLang()
  const env = useTradingEnv()
  // Time in trade keeps counting between polls.
  const now = useNow(POLL_MS)
  const positions = usePositions(env)
  const summary = useSummary(env)
  const { connection } = useConnection(env)
  const canClose = isVerified(connection)
  const [closeSheet, setCloseSheet] = useState<CloseSheetState>(null)
  const showClose = (position: Position) =>
    (prev: CloseSheetState): CloseSheetState => ({ position, open: true, n: (prev?.n ?? 0) + 1 })
  const hideClose = (prev: CloseSheetState): CloseSheetState => (prev ? { ...prev, open: false } : prev)
  const [instId, clearInstFilter] = useInstFilterParam()

  const retry = () => {
    void positions.refetch()
    void summary.refetch()
  }

  if (positions.isError && !positions.data) {
    return <StateBlock kind="error" title={t('error.title')} body={t('error.body')} onRetry={retry} />
  }

  const data = positions.data?.data
  if (data && data.taken_at === null) {
    return <StateBlock kind="empty" title={t('empty.title')} body={t('empty.body')} />
  }

  const loading = positions.isPending
  const items = data?.items ?? []
  const shown = instId ? items.filter((p) => p.inst_id === instId) : items
  const s = summary.data?.data

  const chip = instId && (
    <InstFilterChip instId={instId} shown={shown.length} total={items.length} onClear={clearInstFilter} />
  )

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-6 pt-6 pb-4">
      <div className="grid gap-1">
        {loading ? (
          <Skeleton style={{ width: 200, height: 26 }} />
        ) : (
          shown.length > 0 && (
            <h2 data-testid="positions-summary" className="text-title-2">
              {t('openPositions', { count: shown.length })}
            </h2>
          )
        )}
        {!loading && shown.length > 0 && s && (
          <p className="flex items-baseline gap-2 text-title-3 font-semibold [&_svg]:size-4">
            <span className="text-subheadline font-medium text-label-secondary">{t('positions.unrealisedTotal')}</span>
            <span data-testid="positions-upl-total">
              <SignedValue value={s.unrealised_pnl_usd} unit="USD" />
            </span>
          </p>
        )}
      </div>
      <div>{s ? <ReconciledIndicator summary={s} lang={lang} now={now} /> : <Skeleton style={{ width: 250, height: 26 }} />}</div>
    </header>
  )

  if (loading) {
    return (
      <div aria-busy="true">
        <span className="sr-only" role="status">
          {t('loading')}
        </span>
        {head}
        <TableSkeleton />
      </div>
    )
  }

  if (items.length === 0 && !instId) {
    return (
      <div>
        {head}
        <div className="pf-panel">
          <StateBlock kind="none" compact icon={Layers} title={t('positions.empty.title')} body={t('positions.empty.body')} />
        </div>
      </div>
    )
  }

  if (instId && shown.length === 0) {
    return (
      <div>
        {head}
        {chip}
        <div className="pf-panel" data-testid="positions-filtered-empty">
          <StateBlock
            kind="none"
            compact
            icon={Layers}
            title={t('positions.filteredEmpty.title', { inst: instId })}
            body={t('positions.filteredEmpty.body')}
            actions={
              <>
                <Button asChild size="sm" variant="secondary" data-testid="positions-show-history">
                  <Link to={historyPathForInst(instId)}>{t('positions.filteredEmpty.history')}</Link>
                </Button>
                <Button size="sm" variant="plain" onClick={clearInstFilter} data-testid="positions-show-all">
                  {t('positions.filteredEmpty.showAll')}
                </Button>
              </>
            }
          />
        </div>
      </div>
    )
  }

  return (
    <div>
      {head}
      {chip}
      {!canClose && (
        <Notice
          tone="info"
          icon={Unplug}
          className="mb-4"
          title={t('positions.close.disconnected')}
          actions={
            <Button asChild size="sm" variant="plain">
              <Link to="/settings/connections">{t('positions.close.connect')}</Link>
            </Button>
          }
        />
      )}
      <div className="pf-panel">
        <table className="pf-table pf-positions" data-testid="positions-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c} scope="col" data-col={c} className={NUMERIC.has(c) ? 'is-num' : undefined}>
                  {t(`positions.col.${c}`)}
                </th>
              ))}
              {canClose && (
                <th scope="col" data-col="actions">
                  <span className="sr-only">{t('positions.col.actions')}</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <PositionRow key={p.id} p={p} lang={lang} now={now} canClose={canClose} onClose={(pos) => setCloseSheet(showClose(pos))} />
            ))}
          </tbody>
        </table>
      </div>
      {closeSheet && (
        <ClosePositionSheet
          key={closeSheet.n}
          position={closeSheet.position}
          open={closeSheet.open}
          lang={lang}
          onOpenChange={(o) => {
            if (!o) setCloseSheet(hideClose)
          }}
          onClosed={(position) => {
            setCloseSheet(hideClose)
            void positions.refetch()
            toast({ tone: 'success', title: t('positions.close.toast', { inst: position.inst_id }) })
          }}
        />
      )}
    </div>
  )
}
