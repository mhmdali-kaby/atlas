import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CircleX } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { api } from '../../lib/api'
import { formatPrice, type Lang } from '../../lib/format'
import { Button, Sheet, SheetClose, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice } from '../onboarding'
import { portfolioKeys, type OpenOrder } from './api'
import { cancelErrorMessage } from './orderErrors'
import { shortId, sizeText, typeText } from './ordersFormat'
import { Fig } from './OrdersTables'

/*
 * The Cancel confirmation for one open order (ATL-150): the order's facts, then
 * POST /api/orders/{id}/cancel; on success it closes, toasts and refetches the list, and an
 * API error stays in the sheet with the server's message.
 */

export function CancelOrderSheet({ order, lang, onClose }: { order: OpenOrder; lang: Lang; onClose: () => void }) {
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
      void client.invalidateQueries({
        queryKey: portfolioKeys.orders(env),
      })
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
              {t('proposalLabel')} <Fig>{shortId(order.proposal_id)}</Fig>
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
          {t(`sheet.title_${order.side}`, {
            inst: '⁦' + order.inst_id + '⁩',
          })}
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
            className={cx(
              'flex items-baseline justify-between gap-4 px-3.5 py-2.5',
              i > 0 && 'border-t-[0.5px] border-separator',
            )}
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
