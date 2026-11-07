import { useMutation } from '@tanstack/react-query'
import { CircleAlert, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../lib/api'
import { formatDecimal, formatPrice, type Lang } from '../../lib/format'
import { Notice } from '../onboarding'
import { Button, Sheet, SheetClose, SignedValue } from '../../ui'
import type { ClosePositionReport, Position } from './api'

/*
 * ATL-151 (brief §13 "Close", design §9): the Positions "Close" sheet. Confirm sends a
 * reduce-only market close through the Executor (ATL-148's POST /api/portfolio/positions/
 * {id}/close {confirm:true}); a failure answers non-2xx and the sheet stays open with OKX's
 * message. The response never carries a fill price or a realised PnL (ATL-143's close report
 * has neither), so the success toast names the instrument only.
 */

/** The message to show for a failed close: Laravel's own `message`/`detail`, else the first
 * OKX error in the report's `failed` list, else a generic fallback. */
export function closeFailureMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const body = err.body as { message?: unknown; detail?: unknown; failed?: unknown } | null
    if (body && typeof body.message === 'string' && body.message.trim()) return body.message
    if (body && typeof body.detail === 'string' && body.detail.trim()) return body.detail
    const failed = body && Array.isArray(body.failed) ? (body.failed as Array<{ message?: unknown }>) : []
    const first = failed.find((f) => typeof f.message === 'string' && f.message.trim())
    if (first) return first.message as string
  }
  return fallback
}

export function ClosePositionSheet({
  position,
  open,
  lang,
  onOpenChange,
  onClosed,
}: {
  /** The row's snapshot at the moment Close was pressed; kept while the sheet animates out. */
  position: Position
  open: boolean
  lang: Lang
  onOpenChange: (open: boolean) => void
  onClosed: (position: Position) => void
}) {
  const { t } = useTranslation('portfolio')
  const close = useMutation<ClosePositionReport, unknown>({
    mutationFn: () => api.post<ClosePositionReport>(`/api/portfolio/positions/${position.id}/close`, { confirm: true }),
    onSuccess: () => onClosed(position),
  })

  const side = position.side === 'short' ? 'short' : 'long'
  const errorMessage = close.isError ? closeFailureMessage(close.error, t('positions.close.errorGeneric')) : null

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !close.isPending && onOpenChange(next)}
      tone="destructive"
      icon={<X strokeWidth={1.5} />}
      title={t(`positions.close.title.${side}`, { inst: position.inst_id })}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={close.isPending}>
              {t('positions.close.keep')}
            </Button>
          </SheetClose>
          <Button variant="destructive" loading={close.isPending} onClick={() => close.mutate()} data-testid="close-confirm">
            {close.isPending ? t('positions.close.doing') : t('positions.close.do')}
          </Button>
        </>
      }
    >
      <dl className="grid gap-px overflow-hidden rounded-card bg-fill-4 text-subheadline">
        <div className="flex min-h-10 items-center justify-between gap-4 bg-background-secondary px-4 py-2">
          <dt className="text-label-secondary">{t('positions.col.size')}</dt>
          <dd className="fig font-medium">
            {formatDecimal(position.size, lang, 0)} {t('positions.contractsUnit')}
          </dd>
        </div>
        <div className="flex min-h-10 items-center justify-between gap-4 bg-background-secondary px-4 py-2">
          <dt className="text-label-secondary">{t('positions.col.entry')}</dt>
          <dd className="fig font-medium">{formatPrice(position.entry, lang)}</dd>
        </div>
        <div className="flex min-h-10 items-center justify-between gap-4 bg-background-secondary px-4 py-2">
          <dt className="text-label-secondary">{t('positions.col.mark')}</dt>
          <dd className="fig font-medium">{formatPrice(position.mark, lang)}</dd>
        </div>
        <div className="flex min-h-10 items-center justify-between gap-4 bg-background-secondary px-4 py-2">
          <dt className="text-label-secondary">{t('positions.col.upl')}</dt>
          <dd className="flex items-baseline gap-1.5 font-medium">
            <SignedValue value={position.upl_usd} unit="USD" />
            <SignedValue value={position.upl_pct} unit="percent" className="text-footnote" />
          </dd>
        </div>
      </dl>
      <p className="text-subheadline text-label-secondary">{t('positions.close.text')}</p>
      <p className="text-footnote text-label-secondary">{t('positions.close.algoNote')}</p>
      {errorMessage && (
        <Notice tone="error" icon={CircleAlert} role="alert" title={t('positions.close.failTitle')} data-testid="close-error">
          {errorMessage}
        </Notice>
      )}
    </Sheet>
  )
}
