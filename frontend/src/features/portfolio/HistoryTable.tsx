import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ExternalLink } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDecimal, formatMoney, formatPrice, NO_VALUE, type Lang } from '../../lib/format'
import { SignedValue } from '../../ui'
import { cx } from '../../ui/cx'
import { isoMs, type ClosedRow, type FillRow, type HistoryType } from './historyApi'
import { Fig } from './OrdersTables'
import { shortId, sizeText } from './ordersFormat'
import { Duration, InstId, SideBadge } from './parts'
import { formatDayTime } from './time'

/*
 * The virtualised History table (design v14 #/portfolio/history): TanStack Table owns the columns
 * and rows, TanStack Virtual keeps only the visible window (fixed 44px rows) inside a scroll box
 * whose header sticks. Spacer rows above and below hold the scroll height.
 */

export const ROW_H = 44
const OVERSCAN = 8
const USD_LIKE = new Set(['USD', 'USDT', 'USDC'])

interface ColMeta {
  num?: boolean
}

/** A PnL in its settle currency: USD-like currencies format as $; anything else keeps its code. */
function Pnl({ value, ccy }: { value: string | null; ccy: string | null }) {
  const unit = ccy === null || USD_LIKE.has(ccy) ? 'USD' : ccy
  return <SignedValue value={value} unit={unit} />
}

function Muted({ tip }: { tip: string }) {
  return (
    <span className="text-label-secondary" tabIndex={0} title={tip} aria-label={tip} role="note">
      {NO_VALUE}
    </span>
  )
}

function When({ ts, lang }: { ts: string; lang: Lang }) {
  return <span className="tabular-nums">{formatDayTime(isoMs(ts), lang)}</span>
}

/** R with 2 decimals and its sign, the unit inside the figure ("−1.50R"); "—" with a reason when null. */
export function RCell({ r }: { r: string | null }) {
  const { t } = useTranslation('portfolio-history')
  if (r === null) return <Muted tip={t('noR')} />
  return (
    <Fig className="ph-r">
      {`${formatDecimal(r, 'en', 2, 'exceptZero')}R`}
    </Fig>
  )
}

function useColumns(type: HistoryType, lang: Lang) {
  const { t } = useTranslation(['portfolio-history', 'warroom'])
  return useMemo(() => {
    const agent = (a: string | null) =>
      a === null ? <Muted tip={t('noAgent')} /> : <span>{t(`warroom:agents.${a}.name`, { defaultValue: a })}</span>
    if (type === 'closed') {
      const cols: ColumnDef<ClosedRow>[] = [
        { id: 'closed', cell: ({ row }) => <When ts={row.original.closed_at} lang={lang} /> },
        { id: 'inst', cell: ({ row }) => <InstId id={row.original.inst_id} /> },
        { id: 'side', cell: ({ row }) => <SideBadge side={row.original.side} label={t(row.original.side)} /> },
        { id: 'entry', meta: { num: true }, cell: ({ row }) => <Fig>{formatPrice(row.original.entry, lang)}</Fig> },
        { id: 'exit', meta: { num: true }, cell: ({ row }) => <Fig>{formatPrice(row.original.exit, lang)}</Fig> },
        { id: 'size', meta: { num: true }, cell: ({ row }) => <Fig>{sizeText(row.original.size, lang)}</Fig> },
        {
          id: 'pnl',
          meta: { num: true },
          cell: ({ row }) => <Pnl value={row.original.realised_pnl} ccy={row.original.ccy} />,
        },
        { id: 'r', meta: { num: true }, cell: ({ row }) => <RCell r={row.original.r} /> },
        {
          id: 'held',
          meta: { num: true },
          cell: ({ row }) => {
            const o = row.original
            if (!o.opened_at) return <span className="text-label-secondary">{NO_VALUE}</span>
            return <Duration ms={Math.max(0, isoMs(o.closed_at) - isoMs(o.opened_at))} lang={lang} />
          },
        },
        { id: 'agent', cell: ({ row }) => agent(row.original.agent) },
        {
          id: 'close',
          cell: ({ row }) => {
            const c = row.original.close_type
            return <span>{c === null ? NO_VALUE : t(`closeType.${c}`, { defaultValue: c })}</span>
          },
        },
      ]
      return cols as ColumnDef<ClosedRow | FillRow>[]
    }
    const cols: ColumnDef<FillRow>[] = [
      { id: 'time', cell: ({ row }) => <When ts={row.original.ts} lang={lang} /> },
      { id: 'inst', cell: ({ row }) => <InstId id={row.original.inst_id} /> },
      { id: 'side', cell: ({ row }) => <SideBadge side={row.original.side} label={t(row.original.side)} /> },
      { id: 'price', meta: { num: true }, cell: ({ row }) => <Fig>{formatPrice(row.original.price, lang)}</Fig> },
      { id: 'size', meta: { num: true }, cell: ({ row }) => <Fig>{sizeText(row.original.size, lang)}</Fig> },
      {
        id: 'fee',
        meta: { num: true },
        cell: ({ row }) => {
          const { fee, fee_ccy } = row.original
          if (fee === null) return <span className="text-label-secondary">{NO_VALUE}</span>
          // OKX signs a paid fee negative; the column reads "fee paid", so it shows the amount.
          const abs = fee.trim().replace(/^[-+]/, '')
          const text = fee_ccy === null || USD_LIKE.has(fee_ccy) ? formatMoney(abs, 'USD', lang) : `${formatDecimal(abs, lang, 0)} ${fee_ccy}`
          return <Fig className="text-label-secondary">{text}</Fig>
        },
      },
      {
        id: 'order',
        cell: ({ row }) =>
          row.original.origin === 'external' ? (
            <span className="pf-badge pf-badge-quiet" tabIndex={0} title={t('externalTip')}>
              <ExternalLink aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
              {t('external')}
              <span className="sr-only">. {t('externalTip')}</span>
            </span>
          ) : (
            <span className="pf-badge">{t('atlas')}</span>
          ),
      },
      {
        id: 'proposal',
        cell: ({ row }) =>
          row.original.proposal_id ? (
            <Fig className="text-footnote text-label-secondary">{shortId(row.original.proposal_id)}</Fig>
          ) : (
            <Muted tip={t('noProposal')} />
          ),
      },
    ]
    return cols as ColumnDef<ClosedRow | FillRow>[]
  }, [type, lang, t])
}

export function HistoryTable({
  type,
  rows,
  lang,
  hasMore,
  loadingMore,
  onNearEnd,
}: {
  type: HistoryType
  rows: ReadonlyArray<ClosedRow | FillRow>
  lang: Lang
  hasMore: boolean
  loadingMore: boolean
  onNearEnd: () => void
}) {
  const { t } = useTranslation('portfolio-history')
  const columns = useColumns(type, lang)
  const data = useMemo(() => [...rows], [rows])
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel(), getRowId: (r) => r.id })
  const tableRows = table.getRowModel().rows
  const boxRef = useRef<HTMLDivElement>(null)
  const headRef = useRef<HTMLTableSectionElement>(null)

  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => boxRef.current,
    estimateSize: () => ROW_H,
    overscan: OVERSCAN,
    initialRect: { width: 1200, height: 600 },
    getItemKey: (i) => tableRows[i]!.id,
  })
  const items = virtualizer.getVirtualItems()
  const last = items.length > 0 ? items[items.length - 1]!.index : -1

  useEffect(() => {
    if (hasMore && !loadingMore && last >= tableRows.length - OVERSCAN * 2) onNearEnd()
  }, [last, tableRows.length, hasMore, loadingMore, onNearEnd])

  // Back to the top whenever the tab or the filters swap the list.
  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = 0
  }, [type, columns])

  const padTop = items.length > 0 ? items[0]!.start : 0
  const padBottom = items.length > 0 ? virtualizer.getTotalSize() - items[items.length - 1]!.end : 0
  const nCols = columns.length

  return (
    <div className="pf-panel ph-panel">
      <div
        ref={boxRef}
        className="ph-scroll"
        tabIndex={0}
        role="region"
        aria-label={t('tableLabel', { type: t(`tab.${type}`) })}
        data-testid="history-scroll"
      >
        <table className={cx('pf-table ph-table', `is-${type}`)} data-testid="history-table" aria-rowcount={tableRows.length + 1}>
          <thead ref={headRef}>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} aria-rowindex={1}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    scope="col"
                    data-col={h.column.id}
                    className={(h.column.columnDef.meta as ColMeta | undefined)?.num ? 'is-num' : undefined}
                  >
                    {t(`col.${h.column.id}`)}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {padTop > 0 && (
              <tr className="ph-pad" aria-hidden="true">
                <td colSpan={nCols} style={{ height: padTop }} />
              </tr>
            )}
            {items.map((vi) => {
              const row = tableRows[vi.index]!
              const o = row.original
              const outcome = 'outcome' in o ? o.outcome : null
              return (
                <tr key={row.id} data-testid="history-row" data-id={o.id} data-outcome={outcome ?? undefined} aria-rowindex={vi.index + 2} style={{ height: ROW_H }}>
                  {row.getVisibleCells().map((c) => (
                    <td key={c.id} data-col={c.column.id} className={(c.column.columnDef.meta as ColMeta | undefined)?.num ? 'is-num' : undefined}>
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  ))}
                </tr>
              )
            })}
            {padBottom > 0 && (
              <tr className="ph-pad" aria-hidden="true">
                <td colSpan={nCols} style={{ height: padBottom }} />
              </tr>
            )}
          </tbody>
        </table>
        {loadingMore && (
          <p className="ph-more" role="status">
            {t('loadingMore')}
          </p>
        )}
      </div>
    </div>
  )
}
