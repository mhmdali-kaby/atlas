import { Banknote, Check, TrendingDown, TrendingUp } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { LiveTag } from '../../../app/shell/LiveTag'
import { formatFixed, formatMoney, type Lang } from '../../../lib/format'
import { Button, Sheet, SheetClose } from '../../../ui'
import { fillSlots, slot } from '../slots'
import { InstrumentId } from '../text'
import { decimalsOf, directionOf, stakeOf, zoneOf, type ProposalSummary } from './proposal'

/*
 * The real-order sheet (ATL-240, decision 255 (7), design v21 wr-proposal `liveSheet`). In live,
 * Approve never sends at once: this sheet names what goes to OKX live, and only "Approve real
 * order" sends, with `live_confirmed: true`. Cancel sends nothing. Demo never opens it.
 */

export type LiveSheetKind = 'entry' | 'amend_stop' | 'amend_targets' | 'partial_close' | 'close'

/** The proposal's kind as the sheet names it. */
export function liveSheetKind(kind: string): LiveSheetKind {
  return kind === 'amend_stop' || kind === 'amend_targets' || kind === 'partial_close' || kind === 'close' ? kind : 'entry'
}

/** A live proposal needs the real-order sheet before Approve sends (ATL-238's 422 otherwise). */
export const needsLiveConfirm = (p: Pick<ProposalSummary, 'env'>) => p.env === 'live'

function Fig({ children }: { children: ReactNode }) {
  return (
    <bdi dir="ltr" className="num fig">
      {children}
    </bdi>
  )
}

function Row({ k, v, sub, id }: { k: string; v: ReactNode; sub?: ReactNode; id: string }) {
  return (
    <div className="lvo-row" data-testid={`lvo-${id}`}>
      <dt>{k}</dt>
      <dd>
        <span className="lvo-v">{v}</span>
        {sub ? <span className="lvo-s">{sub}</span> : null}
      </dd>
    </div>
  )
}

export function LiveOrderSheet({
  p,
  open,
  onOpenChange,
  onConfirm,
  lang,
  band,
}: {
  p: ProposalSummary
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  lang: Lang
  /** The approve-by band, still draining while the sheet is open. */
  band?: ReactNode
}) {
  const { t } = useTranslation('proposal')
  const kind = liveSheetKind(p.kind)
  const dir = directionOf(p.side)
  const DirIcon = dir === 'short' ? TrendingDown : TrendingUp
  const d = decimalsOf(p)
  const P = (v: string | null) => (v === null ? '—' : formatFixed(v, d, lang))
  const money = (v: string | null) => (v === null ? '—' : formatMoney(v, 'USD', lang))
  const stake = stakeOf(p)

  const inst = (
    <>
      <span className={`pr-dir is-${dir}`}>
        <DirIcon className="icon" aria-hidden="true" />
        <span>{t(`dir.${dir}`)}</span>
      </span>
      <InstrumentId id={p.inst_id} />
    </>
  )

  const rows: ReactNode[] = [<Row key="inst" id="inst" k={t('lv.inst')} v={inst} />]
  if (kind === 'entry') {
    const zone = zoneOf(p)
    const zoneText = zone ? `${P(zone[0])}–${P(zone[1])}` : P(p.entry)
    const type = p.order_type ? t(`order.${p.order_type}`, { defaultValue: p.order_type }) : '—'
    rows.push(
      <Row
        key="order"
        id="order"
        k={t('lv.order')}
        v={
          <span>
            {fillSlots(t('lv.orderV', { side: slot('side'), type: slot('type'), zone: slot('zone') }), {
              side: t(`lv.side.${p.side}`),
              type,
              zone: <Fig>{zoneText}</Fig>,
            })}
          </span>
        }
      />,
      <Row
        key="size"
        id="size"
        k={t('lv.size')}
        v={<Fig>{money(stake.notional)}</Fig>}
        sub={
          stake.contracts !== null
            ? fillSlots(t('lv.sizeSub', { c: slot('c'), lev: slot('lev') }), {
                c: <Fig>{stake.contracts}</Fig>,
                lev: <Fig>{stake.leverage !== null ? `${stake.leverage}×` : '—'}</Fig>,
              })
            : undefined
        }
      />,
      <Row
        key="risk"
        id="risk"
        k={t('atRisk')}
        v={<Fig>{money(stake.risk)}</Fig>}
        sub={stake.riskPct !== null ? fillSlots(t('atRiskSub', { pct: slot('pct') }), { pct: <Fig>{`${formatFixed(stake.riskPct, 2, lang)}%`}</Fig> }) : undefined}
      />,
      <Row key="stop" id="stop" k={t('lv.stop')} v={<Fig>{P(p.stop)}</Fig>} sub={t('lv.stopSub')} />,
    )
  } else {
    if (kind === 'close' || kind === 'partial_close') {
      const v =
        kind === 'close'
          ? stake.contracts !== null
            ? fillSlots(t('lv.closeV', { c: slot('c') }), { c: <Fig>{stake.contracts}</Fig> })
            : t('lv.closeAll')
          : fillSlots(t('lv.closePart', { pct: slot('pct') }), { pct: <Fig>{`${formatFixed(p.close_pct ?? '0', 0, lang)}%`}</Fig> })
      rows.push(<Row key="close" id="close" k={t('lv.close')} v={v} />)
    } else if (kind === 'amend_stop') {
      rows.push(<Row key="stop" id="stop" k={t('lv.stop')} v={<Fig>{P(p.stop)}</Fig>} sub={t('lv.stopMoveSub')} />)
    } else {
      const list = p.take_profits
        .filter((x) => x.price !== null)
        .map((x) => P(x.price))
        .join(', ')
      rows.push(<Row key="tps" id="tps" k={t('lv.targets')} v={<Fig>{list || '—'}</Fig>} />)
    }
    rows.push(<Row key="position" id="position" k={t('lv.position')} v={<Fig>{money(stake.notional)}</Fig>} />)
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      tone="live"
      icon={<Banknote strokeWidth={1.5} />}
      title={
        <span data-testid="pr-live-title">
          {t(`lv.title.${kind}`)}
          <LiveTag />
        </span>
      }
      description={t(`lv.lead.${kind}`)}
      className="pr-live-sheet"
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" data-testid="pr-live-cancel">
              {t('cancel')}
            </Button>
          </SheetClose>
          <Button variant="live" icon={<Check strokeWidth={1.75} />} onClick={onConfirm} data-testid="pr-live-go">
            {t('lv.confirm')}
          </Button>
        </>
      }
      onOpenAutoFocus={(e) => {
        const go = document.querySelector<HTMLElement>('[data-sheet] [data-testid="pr-live-go"]')
        if (go) {
          e.preventDefault()
          go.focus()
        }
      }}
    >
      <div className="grid gap-4" data-testid="pr-live-sheet">
        <dl className="lvo" aria-label={t('lv.receipt')}>
          {rows}
        </dl>
        {band}
      </div>
    </Sheet>
  )
}
