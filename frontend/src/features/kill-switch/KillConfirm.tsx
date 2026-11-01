import { CirclePause, CircleX, OctagonX, ShieldCheck, Shrink } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatSigned, signOf } from '../../lib/format'
import { Button, SheetClose, Toggle } from '../../ui'
import { cx } from '../../ui/cx'
import type { KillPreview } from './api'
import { HoldButton, type Hint } from './HoldButton'
import { Fig, Inst, NS, sumDecimals, Tr } from './parts'

/** What a Stop will do, with the close-all switch and, when it is on, the positions it closes. */
export function ConfirmBody({
  preview,
  flatten,
  onFlatten,
  account,
  lang,
}: {
  preview: KillPreview | undefined
  flatten: boolean
  onFlatten: (on: boolean) => void
  account: string
  lang: 'en' | 'ar'
}) {
  const { t } = useTranslation(NS)
  const labelId = useId()
  const subId = useId()
  const n = preview?.positions.length
  const upl = preview ? sumDecimals(preview.positions.map((p) => p.upl_usd)) : null
  const cancelText = preview ? (
    flatten ? (
      <Tr
        k="w.cancelAll"
        values={{
          open: preview.openOrders,
          algo: preview.takeProfits + preview.stops,
        }}
      />
    ) : (
      <Tr
        k="w.cancelKeep"
        values={{
          open: preview.openOrders,
          tp: preview.takeProfits,
          account,
        }}
      />
    )
  ) : (
    t(flatten ? 'w.cancelAllAny' : 'w.cancelKeepAny', { account })
  )
  const keepText = preview ? (
    flatten ? (
      <Tr k="w.close" values={{ n }} />
    ) : (
      <Tr k="w.keep" values={{ n: preview.stops }} />
    )
  ) : (
    t(flatten ? 'w.closeAny' : 'w.keepAny')
  )
  const what: { key: string; icon: ReactNode; text: ReactNode }[] = [
    { key: 'cancel', icon: <CircleX />, text: cancelText },
    {
      key: 'keep',
      icon: flatten ? <Shrink /> : <ShieldCheck />,
      text: keepText,
    },
    { key: 'pause', icon: <CirclePause />, text: t('w.pause') },
  ]

  return (
    <>
      <section className="ks-what" aria-labelledby={`${labelId}-what`}>
        <h3 className="ks-h" id={`${labelId}-what`}>
          {t('what')}
        </h3>
        <ul className="ks-list" role="list">
          {what.map((w) => (
            <li key={w.key}>
              <span className="ks-li-ic" aria-hidden="true">
                {w.icon}
              </span>
              <span>{w.text}</span>
            </li>
          ))}
        </ul>
      </section>
      <div className={cx('ks-group ks-flatten', flatten && 'is-on')}>
        <div className="ks-row">
          <div className="ks-row-main">
            <span className="ks-row-label" id={labelId}>
              {n === undefined ? t('flattenAny') : <Tr k="flatten" values={{ n }} />}
            </span>
            <span className="ks-row-sub" id={subId}>
              {upl === null ? (
                t('flattenSubAny')
              ) : (
                <Tr
                  k="flattenSub"
                  values={{
                    upl: formatSigned(upl, 'USD', lang),
                  }}
                />
              )}
            </span>
          </div>
          <Toggle
            checked={flatten}
            onCheckedChange={onFlatten}
            aria-labelledby={labelId}
            aria-describedby={subId}
            data-testid="kill-flatten"
          />
        </div>
        {flatten && preview && preview.positions.length > 0 && (
          <ul className="ks-pos" role="list">
            {preview.positions.map((p) => (
              <li key={p.id}>
                <Inst id={p.inst_id} />
                <span className="ks-side">{t(p.side === 'short' ? 'short' : 'long')}</span>
                <Fig className={cx('ks-upl', signClass(p.upl_usd))}>{formatSigned(p.upl_usd, 'USD', lang)}</Fig>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

function signClass(v: string | null | undefined) {
  const s = signOf(v)
  return s === 1 ? 'is-gain' : s === -1 ? 'is-loss' : undefined
}

/** Cancel, then Stop trading (one press) or, with close-all on, the 1.5 s hold. */
export function ConfirmActions({ flatten, onGo }: { flatten: boolean; onGo: () => void }) {
  const { t } = useTranslation(NS)
  const [hint, setHint] = useState<Hint>('idle')
  const hintId = useId()
  return (
    <>
      {flatten && (
        <p className={cx('ks-hold-hint', hint === 'early' && 'is-early')} id={hintId} aria-live="polite">
          {t(hint === 'early' ? 'holdEarly' : hint === 'holding' ? 'holdKeep' : 'holdHint')}
        </p>
      )}
      <SheetClose>
        <Button variant="secondary" data-ks-cancel="">
          {t('cancel')}
        </Button>
      </SheetClose>
      {flatten ? (
        <HoldButton onComplete={onGo} onHint={setHint} describedBy={hintId} />
      ) : (
        <Button variant="destructive" icon={<OctagonX strokeWidth={1.75} />} onClick={onGo} data-testid="kill-go">
          {t('go')}
        </Button>
      )}
    </>
  )
}
