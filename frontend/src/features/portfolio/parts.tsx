import { ArrowDownRight, ArrowUpRight, CircleCheck, CloudOff, RefreshCw, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button, Seal, Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import type { PortfolioSummary } from './api'
import { formatAgo, formatClock } from './time'

/** Re-exported so the Portfolio pages keep importing it from here; it lives in src/ui. */
export { Skeleton } from '../../ui'

/*
 * Pieces every Portfolio page shares (design: css/portfolio.css): the reconciled
 * indicator, the state blocks and skeletons, the instrument ID and the side badge
 * (hooks are in hooks.ts).
 */

/** OKX instrument ID verbatim: the base asset carries the row, the rest recedes. */
export function InstId({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <span dir="ltr" className="pf-inst">
      {cut < 0 ? id : id.slice(0, cut)}
      {cut >= 0 && <span className="pf-inst-rest">{id.slice(cut)}</span>}
    </span>
  )
}

/**
 * Direction badge: a position's long/short or an order's buy/sell. The caller passes the
 * translated label, because each page words it in its own namespace.
 */
export function SideBadge({ side, label }: { side: 'long' | 'short' | 'buy' | 'sell'; label: string }) {
  const Icon = side === 'long' || side === 'buy' ? ArrowUpRight : ArrowDownRight
  return (
    <span className="pf-badge" data-side={side}>
      <Icon aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
      {label}
    </span>
  )
}

export function ReconciledIndicator({ summary, lang, now }: { summary: PortfolioSummary; lang: Lang; now: number }) {
  const { t } = useTranslation('portfolio')
  const pill = 'inline-flex items-center gap-1.5 rounded-full py-1 ps-2 pe-2.5 text-footnote [&_svg]:size-[15px]'
  if (summary.stale) {
    const at = summary.reconciled_at
    return (
      <p
        data-testid="reconciled"
        data-state="stale"
        tabIndex={0}
        title={t('staleTip')}
        className={cx(pill, 'bg-warning-wash font-medium text-warning-ink')}
      >
        <TriangleAlert aria-hidden="true" strokeWidth={2} />
        <span>
          {at
            ? t('stale', {
                ago: formatAgo(now - Date.parse(at), lang),
              })
            : t('staleNever')}
        </span>
        <span className="sr-only">. {t('staleTip')}</span>
      </p>
    )
  }
  return (
    <p
      data-testid="reconciled"
      data-state="ok"
      tabIndex={0}
      title={t('reconciledTip')}
      aria-live="polite"
      className={cx(pill, 'bg-fill-4 text-label-secondary')}
    >
      <CircleCheck aria-hidden="true" strokeWidth={2} className="text-profit-ink" />
      <span>
        {t('reconciled', {
          time: summary.reconciled_at ? formatClock(summary.reconciled_at, lang) : '—',
        })}
      </span>
    </p>
  )
}

export function StateBlock({
  kind,
  title,
  body,
  compact,
  onRetry,
  icon: Icon = CloudOff,
}: {
  /** empty: still syncing (seal + "Waiting for OKX…"); none: synced, nothing to list; error: retry. */
  kind: 'empty' | 'none' | 'error'
  icon?: LucideIcon
  title: string
  body: string
  compact?: boolean
  onRetry?: () => void
}) {
  const { t } = useTranslation('portfolio')
  const parts = body.split('./atlas up')
  return (
    <div
      role={kind === 'error' ? 'alert' : 'status'}
      data-testid={`state-${kind}`}
      className={cx(
        'mx-auto grid max-w-[520px] justify-items-center gap-2.5 px-6 text-center',
        compact ? 'pt-14 pb-16' : 'pt-[12vh] pb-16',
      )}
    >
      {kind === 'empty' ? (
        <span className="mb-2">
          <Seal seed="okx-demo" size={compact ? 72 : 120} inkClassName="stroke-seal-muted" />
        </span>
      ) : (
        <span
          className={cx(
            'mb-1.5 grid size-14 place-items-center rounded-panel',
            kind === 'error'
              ? 'bg-loss-wash text-loss-ink'
              : 'bg-fill-4 text-label-secondary shadow-[inset_0_0_0_0.5px_var(--hairline-color)]',
          )}
        >
          <Icon aria-hidden="true" className="size-7" strokeWidth={1.5} />
        </span>
      )}
      <h2 className="text-title-3">{title}</h2>
      <p className="max-w-[44ch] text-subheadline text-balance text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code
              dir="ltr"
              className="inline-block whitespace-nowrap rounded-[6px] bg-fill-3 px-1.5 text-label [unicode-bidi:isolate]"
            >
              ./atlas up
            </code>
            {parts[1]}
          </>
        ) : (
          body
        )}
      </p>
      {kind === 'empty' && (
        <p className="mt-1.5 inline-flex items-center gap-2 text-footnote text-label-secondary">
          <Spinner />
          <span>{t('waiting')}</span>
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" className="mt-2" icon={<RefreshCw aria-hidden="true" />} onClick={onRetry}>
          {t('error.retry')}
        </Button>
      )}
    </div>
  )
}
