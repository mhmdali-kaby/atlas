import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDownRight,
  ArrowUpRight,
  CircleAlert,
  CircleDot,
  CircleX,
  Clock,
  Info,
  ListChecks,
  Shield,
  ShieldCheck,
  Split,
  Target,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { api } from '../../lib/api'
import { formatDecimal, formatPrice, type Lang } from '../../lib/format'
import { Button, Sheet, SheetClose, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice } from '../onboarding'
import { POLL_MS, portfolioKeys, useOrders, useSummary, type AlgoOrder, type OpenOrder } from './api'
import { useLang, useNow } from './hooks'
import { cancelErrorMessage } from './orderErrors'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import { formatClock, formatDayTime, formatDuration } from './time'
import './portfolio.css'

/*
 * Portfolio → Orders (brief §13, design v11 #/portfolio/orders, ATL-150). The orders resting
 * on OKX right now, from GET /api/portfolio/orders (ATL-148, polled every 30 s): open orders
 * with a Cancel sheet (POST /api/orders/{id}/cancel), and algo orders read-only, because the
 * API has no route to cancel one on its own (cancelling the entry takes its stop and targets).
 */

const OPEN_COLUMNS = ['instrument', 'side', 'type', 'price', 'size', 'filled', 'state', 'origin', 'age'] as const
const OPEN_NUMERIC = new Set<string>(['price', 'size', 'filled', 'age'])
const ALGO_COLUMNS = ['instrument', 'kind', 'trigger', 'size', 'state'] as const
const ALGO_NUMERIC = new Set<string>(['trigger', 'size'])
const KIND_ICON: Record<AlgoOrder['kind'], LucideIcon> = { sl: Shield, tp: Target, oco: Split, trigger: Zap }
const KNOWN_TYPES = new Set(['limit', 'market', 'post_only', 'fok', 'ioc'])

/** OKX instrument ID verbatim: the base asset carries the row, the rest recedes. */
function InstId({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <span dir="ltr" className="pf-inst">
      {cut < 0 ? id : id.slice(0, cut)}
      {cut >= 0 && <span className="pf-inst-rest">{id.slice(cut)}</span>}
    </span>
  )
}

/** A figure in markup: an LTR isolate with tabular digits (design §12.1). */
function Fig({ children, className }: { children: string; className?: string }) {
  return (
    <bdi dir="ltr" className={cx('fig', className)}>
      {children}
    </bdi>
  )
}

/** Contracts: every digit the API sent, no forced decimals ("12.02", "2"). */
const sizeText = (v: string | null, lang: Lang) => formatDecimal(v, lang, 0)

/** The short form of a proposal UUID shown in the Origin cell. */
const shortId = (id: string) => id.slice(0, 8)

function SideBadge({ side }: { side: OpenOrder['side'] }) {
  const { t } = useTranslation('portfolio-orders')
  const Icon = side === 'buy' ? ArrowUpRight : ArrowDownRight
  return (
    <span className="pf-badge" data-side={side}>
      <Icon aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
      {t(`side.${side}`)}
    </span>
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
          {t('proposal', { id: '' })}
          <Fig>{shortId(o.proposal_id)}</Fig>
        </span>
      )}
    </>
  )
}

function typeText(t: (k: string) => string, ordType: string) {
  return KNOWN_TYPES.has(ordType) ? t(`type.${ordType}`) : ordType
}

function OpenRow({ o, lang, now, onCancel }: { o: OpenOrder; lang: Lang; now: number; onCancel: (o: OpenOrder) => void }) {
  const { t } = useTranslation('portfolio-orders')
  const filled = o.filled_sz ?? '0'
  const pct = Number(o.sz) > 0 ? Math.round((Number(filled) / Number(o.sz)) * 100) : 0
  return (
    <tr data-testid="open-order-row" data-id={o.id} data-origin={o.origin}>
      <td data-col="instrument">
        <InstId id={o.inst_id} />
      </td>
      <td data-col="side">
        <SideBadge side={o.side} />
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
        <span className="fig" tabIndex={0} title={t('placed', { date: formatDayTime(o.created_at, lang) })}>
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
            aria-label={t('cancelAria', { inst: o.inst_id, what: t(`what.${o.side}`) })}
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

function AlgoRow({ a, lang }: { a: AlgoOrder; lang: Lang }) {
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

function SectionHead({ id, title, note }: { id: string; title: string; note: string }) {
  return (
    <div className="grid gap-0.5 px-1 pb-2.5">
      <h3 id={id} className="text-headline">
        {title}
      </h3>
      <p className="max-w-[70ch] text-footnote text-label-secondary">{note}</p>
    </div>
  )
}

function TableSkeleton({ cols, rows, testId }: { cols: number; rows: number; testId: string }) {
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
                  <Skeleton style={{ width: i ? 60 : 150, height: i ? 14 : 28 }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CancelSheet({ order, lang, onClose }: { order: OpenOrder; lang: Lang; onClose: () => void }) {
  const { t } = useTranslation('portfolio-orders')
  const env = useTradingEnv()
  const client = useQueryClient()
  const cancel = useMutation({
    mutationFn: () => api.post<unknown>(`/api/orders/${encodeURIComponent(order.id)}/cancel`),
    onSuccess: () => {
      onClose()
      toast({
        tone: 'success',
        title: t('toast'),
        body: `⁦${order.inst_id}⁩ ${t(`side.${order.side}`)} ${typeText(t, order.ord_type)}${
          order.px === null ? '' : `, ⁦${formatPrice(order.px, lang)}⁩`
        }`,
      })
      void client.invalidateQueries({ queryKey: portfolioKeys.orders(env) })
    },
  })
  const facts: [string, ReactNode][] = [
    [t('col.instrument'), <Fig key="i">{order.inst_id}</Fig>],
    [t('col.side'), t(`side.${order.side}`)],
    [t('col.type'), typeText(t, order.ord_type)],
    [t('col.price'), order.px === null ? t('marketPx') : <Fig key="p">{formatPrice(order.px, lang)}</Fig>],
    [
      t('col.size'),
      <>
        <Fig>{sizeText(order.sz, lang)}</Fig> {t('contracts')}
      </>,
    ],
    [
      t('col.origin'),
      order.origin === 'atlas' ? (
        <>
          {t('atlas')}
          {order.proposal_id && (
            <>
              {t('listSep')}
              {t('proposal', { id: '' })}
              <Fig>{shortId(order.proposal_id)}</Fig>
            </>
          )}
        </>
      ) : (
        t('external')
      ),
    ],
  ]
  return (
    <Sheet
      open
      onOpenChange={(next) => !next && !cancel.isPending && onClose()}
      tone="destructive"
      icon={<CircleX strokeWidth={1.5} />}
      title={
        <span data-testid="cancel-sheet-title">
          {t(`sheet.title_${order.side}`, { inst: '⁦' + order.inst_id + '⁩' })}
        </span>
      }
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={cancel.isPending} data-testid="cancel-keep">
              {t('sheet.keep')}
            </Button>
          </SheetClose>
          <Button
            variant="destructive"
            loading={cancel.isPending}
            data-testid="cancel-confirm"
            onClick={() => !cancel.isPending && cancel.mutate()}
          >
            {cancel.isPending ? t('sheet.doing') : t('sheet.do')}
          </Button>
        </>
      }
    >
      <dl className="m-0 grid rounded-card bg-fill-4 shadow-[inset_0_0_0_0.5px_var(--hairline-color)]">
        {facts.map(([k, v], i) => (
          <div
            key={k}
            className={cx('flex items-baseline justify-between gap-4 px-3.5 py-2.5', i > 0 && 'border-t-[0.5px] border-separator')}
          >
            <dt className="text-footnote text-label-secondary">{k}</dt>
            <dd className="m-0 min-w-0 text-end text-subheadline font-medium">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="text-subheadline text-label-secondary">{t(order.origin === 'atlas' ? 'sheet.atlas' : 'sheet.external')}</p>
      {cancel.isError && (
        <Notice tone="error" icon={CircleAlert} role="alert" title={t('sheet.failTitle')} data-testid="cancel-error">
          <bdi dir="auto" className="block">
            {cancelErrorMessage(cancel.error, t('sheet.failGeneric'))}
          </bdi>
        </Notice>
      )}
    </Sheet>
  )
}

export function OrdersPage() {
  const { t } = useTranslation('portfolio-orders')
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(POLL_MS)
  const orders = useOrders(env)
  const summary = useSummary(env)
  const [cancelling, setCancelling] = useState<OpenOrder | null>(null)

  if (orders.isError && !orders.data) {
    return (
      <StateBlock
        kind="error"
        title={t('error.title')}
        body={t('error.body')}
        onRetry={() => {
          void orders.refetch()
          void summary.refetch()
        }}
      />
    )
  }

  const loading = orders.isPending
  const open = orders.data?.data.orders ?? []
  const algo = orders.data?.data.algo_orders ?? []
  const s = summary.data?.data

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-6 pt-6 pb-4">
      <div className="grid gap-1">
        {loading ? (
          <Skeleton style={{ width: 300, height: 26 }} />
        ) : (
          (open.length > 0 || algo.length > 0) && (
            <h2 data-testid="orders-summary" className="text-title-2">
              {t('summary', { open: t('open', { count: open.length }), algo: t('algo', { count: algo.length }) })}
            </h2>
          )
        )}
      </div>
      <div>{s ? <ReconciledIndicator summary={s} lang={lang} now={now} /> : <Skeleton style={{ width: 250, height: 26 }} />}</div>
    </header>
  )

  return (
    <div aria-busy={loading || undefined}>
      {loading && (
        <span className="sr-only" role="status" data-testid="orders-loading">
          {t('loading')}
        </span>
      )}
      {head}
      <section aria-labelledby="ord-open-title">
        <SectionHead id="ord-open-title" title={t('openTitle')} note={t('openNote')} />
        {loading ? (
          <TableSkeleton cols={OPEN_COLUMNS.length + 1} rows={2} testId="open-orders-loading" />
        ) : open.length === 0 ? (
          <div className="pf-panel">
            <StateBlock kind="none" compact icon={ListChecks} title={t('empty.title')} body={t('empty.body')} />
          </div>
        ) : (
          <div className="pf-panel">
            <table className="pf-table" data-testid="open-orders-table" aria-labelledby="ord-open-title">
              <thead>
                <tr>
                  {OPEN_COLUMNS.map((c) => (
                    <th key={c} scope="col" data-col={c} className={OPEN_NUMERIC.has(c) ? 'is-num' : undefined}>
                      {t(`col.${c}`)}
                    </th>
                  ))}
                  <th scope="col" data-col="actions" className="w-[1%]">
                    <span className="sr-only">{t('col.actions')}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {open.map((o) => (
                  <OpenRow key={o.id} o={o} lang={lang} now={now} onCancel={setCancelling} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section aria-labelledby="ord-algo-title" className="mt-7">
        <SectionHead id="ord-algo-title" title={t('algoTitle')} note={t('algoNote')} />
        {loading ? (
          <TableSkeleton cols={ALGO_COLUMNS.length} rows={3} testId="algo-orders-loading" />
        ) : algo.length === 0 ? (
          <div className="pf-panel">
            <StateBlock kind="none" compact icon={Shield} title={t('algoEmpty.title')} body={t('algoEmpty.body')} />
          </div>
        ) : (
          <div className="pf-panel">
            <table className="pf-table" data-testid="algo-orders-table" aria-labelledby="ord-algo-title">
              <thead>
                <tr>
                  {ALGO_COLUMNS.map((c) => (
                    <th key={c} scope="col" data-col={c} className={ALGO_NUMERIC.has(c) ? 'is-num' : undefined}>
                      {t(`col.${c}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {algo.map((a) => (
                  <AlgoRow key={a.id} a={a} lang={lang} />
                ))}
              </tbody>
            </table>
            <p className="flex items-start gap-2 border-t-[0.5px] border-separator px-5 py-3 text-footnote text-label-secondary [&_svg]:mt-0.5 [&_svg]:size-3.5 [&_svg]:shrink-0">
              <Info aria-hidden="true" strokeWidth={2} />
              {t('readOnlyTip')}
            </p>
          </div>
        )}
      </section>
      {cancelling && <CancelSheet order={cancelling} lang={lang} onClose={() => setCancelling(null)} />}
    </div>
  )
}

