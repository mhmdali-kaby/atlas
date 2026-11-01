import { CircleDot, Clock, Info, Shield, ShieldCheck, Split, Target, Zap, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatDecimal, formatPrice, type Lang } from '../../lib/format'
import { shortId, sizeText, typeText } from './ordersFormat'
import { Button } from '../../ui'
import { cx } from '../../ui/cx'
import type { AlgoOrder, OpenOrder } from './api'
import { InstId, SideBadge, Skeleton } from './parts'
import { formatClock, formatDayTime, formatDuration } from './time'

/*
 * The two tables of Portfolio → Orders (ATL-150): the open-orders row with its Cancel
 * button, the algo-order row and its trigger cell, the skeleton shown while loading, and
 * the figure isolate the Cancel sheet shares with them (column lists: ordersFormat.ts).
 */

const KIND_ICON: Record<AlgoOrder['kind'], LucideIcon> = { sl: Shield, tp: Target, oco: Split, trigger: Zap }

/** A figure in markup: an LTR isolate with tabular digits (design §12.1). */
export function Fig({ children, className }: { children: string; className?: string }) {
  return (
    <bdi dir="ltr" className={cx('fig', className)}>
      {children}
    </bdi>
  )
}

function OriginCell({ o }: { o: OpenOrder }) {
  const { t } = useTranslation('portfolio-orders')
  if (o.origin === 'external') {
    return (
      <span
        data-testid="external-badge"
        tabIndex={0}
        title={t('externalTip')}
        className="pf-badge pf-badge-quiet text-label focus-visible:shadow-(--focus-ring) focus-visible:outline-none"
      >
        <Info aria-hidden="true" strokeWidth={2} />
        {t('external')}
        <span className="sr-only">. {t('externalTip')}</span>
      </span>
    )
  }
  return (
    <>
      <span className="block font-medium">{t('atlas')}</span>
      {o.proposal_id && (
        <span className="pf-cell-sub">
          {t('proposalLabel')} <Fig>{shortId(o.proposal_id)}</Fig>
        </span>
      )}
    </>
  )
}

export function OpenRow({ o, lang, now, onCancel }: { o: OpenOrder; lang: Lang; now: number; onCancel: (o: OpenOrder) => void }) {
  const { t } = useTranslation('portfolio-orders')
  const filled = o.filled_sz ?? '0'
  const pct = Number(o.sz) > 0 ? Math.round((Number(filled) / Number(o.sz)) * 100) : 0
  return (
    <tr data-testid="open-order-row" data-id={o.id} data-origin={o.origin}>
      <td data-col="instrument">
        <InstId id={o.inst_id} />
      </td>
      <td data-col="side">
        <SideBadge side={o.side} label={t(`side.${o.side}`)} />
      </td>
      <td data-col="type">{typeText(t, o.ord_type)}</td>
      <td data-col="price" className="is-num fig">
        {o.px === null ? <span className="font-sans">{t('marketPx')}</span> : formatPrice(o.px, lang)}
      </td>
      <td data-col="size" className="is-num">
        <span className="fig">{sizeText(o.sz, lang)}</span>
        <span className="pf-cell-sub">{t('contracts')}</span>
      </td>
      <td data-col="filled" className="is-num">
        <span className="fig">{sizeText(filled, lang)}</span>
        <span className="pf-cell-sub">
          <Fig>{`${formatDecimal(String(pct), lang, 0)}%`}</Fig>
        </span>
      </td>
      <td data-col="state">
        <span className="inline-flex items-center gap-1.5 [&_svg]:size-3.5 [&_svg]:text-label-secondary">
          <CircleDot aria-hidden="true" strokeWidth={2} />
          {t(`state.${o.state}`)}
        </span>
      </td>
      <td data-col="origin">
        <OriginCell o={o} />
      </td>
      <td data-col="age" className="is-num">
        <span
          className="fig"
          tabIndex={0}
          title={t('placed', {
            date: formatDayTime(o.created_at, lang),
          })}
        >
          {formatDuration(Math.max(60_000, now - Date.parse(o.created_at)), lang)}
        </span>
        <span className="pf-cell-sub fig">{formatClock(o.created_at, lang, false)}</span>
      </td>
      <td data-col="actions" className="w-[1%] text-end">
        {o.can_cancel && (
          <Button
            variant="plain-destructive"
            size="sm"
            data-testid="cancel-order"
            aria-label={t('cancelAria', {
              inst: o.inst_id,
              what: t(`what.${o.side}`),
            })}
            onClick={() => onCancel(o)}
          >
            {t('cancel')}
          </Button>
        )}
      </td>
    </tr>
  )
}

function TriggerCell({ a, lang }: { a: AlgoOrder; lang: Lang }) {
  const { t } = useTranslation('portfolio-orders')
  const line = (label: string, v: string | null) => (
    <span className="flex items-baseline justify-end gap-1.5">
      <span className="font-sans text-footnote text-label-secondary">{label}</span>
      <Fig>{formatPrice(v, lang)}</Fig>
    </span>
  )
  if (a.kind === 'oco') {
    return (
      <>
        {line(t('stopAt'), a.sl_trigger_px)}
        {line(t('tpAt'), a.tp_trigger_px)}
      </>
    )
  }
  return <Fig>{formatPrice(a.trigger_px ?? a.sl_trigger_px ?? a.tp_trigger_px, lang)}</Fig>
}

export function AlgoRow({ a, lang }: { a: AlgoOrder; lang: Lang }) {
  const { t } = useTranslation('portfolio-orders')
  const Icon = KIND_ICON[a.kind] ?? Zap
  return (
    <tr data-testid="algo-order-row" data-id={a.id} data-kind={a.kind} data-origin={a.origin}>
      <td data-col="instrument">
        <InstId id={a.inst_id} />
        {a.origin === 'external' && (
          <span className="pf-cell-sub">
            <span data-testid="external-badge" className="pf-badge pf-badge-quiet" title={t('externalTip')}>
              {t('external')}
            </span>
          </span>
        )}
      </td>
      <td data-col="kind">
        <span className="inline-flex items-start gap-2">
          <Icon
            aria-hidden="true"
            strokeWidth={1.75}
            className={cx('mt-0.5 size-4 shrink-0', a.protects ? 'text-label' : 'text-label-secondary')}
          />
          <span>
            <span className="block">{t(`kind.${a.kind}`)}</span>
            {a.kind === 'oco' && <span className="pf-cell-sub">{t('kind.ocoSub')}</span>}
            {a.protects && (
              <span className="pf-cell-sub">
                <span className="inline-flex items-center gap-1 [&_svg]:size-3">
                  <ShieldCheck aria-hidden="true" strokeWidth={2} />
                  {t('protects')}
                </span>
              </span>
            )}
          </span>
        </span>
      </td>
      <td data-col="trigger" className="is-num">
        <TriggerCell a={a} lang={lang} />
      </td>
      <td data-col="size" className="is-num">
        {a.sz === null ? (
          <span className="font-sans">{t('wholePosition')}</span>
        ) : (
          <>
            <span className="fig">{sizeText(a.sz, lang)}</span>
            <span className="pf-cell-sub">{t('contracts')}</span>
          </>
        )}
      </td>
      <td data-col="state">
        <span className="inline-flex items-center gap-1.5 [&_svg]:size-3.5 [&_svg]:text-label-secondary">
          <Clock aria-hidden="true" strokeWidth={2} />
          {t(a.state === 'live' ? 'state.algo_live' : `state.${a.state}`)}
        </span>
      </td>
    </tr>
  )
}

export function TableSkeleton({ cols, rows, testId }: { cols: number; rows: number; testId: string }) {
  return (
    <div className="pf-panel" data-testid={testId}>
      <table className="pf-table">
        <thead>
          <tr>
            {Array.from({ length: cols }, (_, i) => (
              <th key={i}>
                <Skeleton style={{ width: 60, height: 12 }} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, i) => (
                <td key={i}>
                  <Skeleton
                    style={{
                      width: i ? 60 : 150,
                      height: i ? 14 : 28,
                    }}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
