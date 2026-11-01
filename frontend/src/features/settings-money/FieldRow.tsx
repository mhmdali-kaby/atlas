import { CircleAlert, Minus, Plus, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { cx } from '../../ui/cx'
import { BindsBadge } from './Preview'
import { atBound, clean, fieldError, isInteger, isNullable, toCents, type MoneyField, type MoneyTexts } from './model'
import { Fig, fill, usd } from './parts'

/* One stepper row of the "Limits in USDT" group (design §5.5). */

interface Props {
  field: MoneyField
  texts: MoneyTexts
  changed: boolean
  binds: boolean
  /** A server 422 message, shown instead of a client error. */
  serverError?: string
  usedToday: bigint | null
  tradesToday: number | null
  lang: Lang
  onChange: (text: string) => void
  onStep: (dir: 1 | -1) => void
  onBlur: () => void
  onCap: (set: boolean) => void
}

function useSub(field: MoneyField, texts: MoneyTexts, usedToday: bigint | null, tradesToday: number | null, lang: Lang, onCap: (set: boolean) => void): { node: ReactNode; warn?: boolean } {
  const { t } = useTranslation('settings-money')
  const raw = texts[field]
  if (field === 'bot_capital_cap_usdt') {
    const none = clean(raw) === ''
    return {
      node: (
        <>
          {t(none ? 'sub.capNone' : 'sub.cap')}{' '}
          <button type="button" className="ml-cap-act" onClick={() => onCap(none)}>
            {t(none ? 'capSet' : 'capClear')}
          </button>
        </>
      ),
    }
  }
  if (field === 'max_usdt_per_trade') return { node: t('sub.trade') }
  if (field === 'max_new_exposure_per_day_usdt') {
    if (usedToday === null || usedToday === 0n) return { node: t('sub.dayNone') }
    const used = <Fig>{usd(usedToday, lang)}</Fig>
    if (fieldError(field, raw)) return { node: fill(t('sub.dayOnly'), { used }) }
    const cap = toCents(raw) ?? 0n
    const left = cap > usedToday ? cap - usedToday : 0n
    if (left === 0n) return { node: t('sub.dayUsed'), warn: true }
    return { node: fill(t('sub.day'), { used, left: <Fig>{usd(left, lang)}</Fig> }) }
  }
  const n = tradesToday ?? 0
  const max = fieldError(field, raw) ? Infinity : Number(clean(raw))
  if (tradesToday !== null && n >= max) return { node: fill(t('sub.tradesAll'), { n: <Fig>{n}</Fig> }), warn: true }
  return { node: fill(t('sub.trades', { count: n }), { n: <Fig>{n}</Fig> }) }
}

export function FieldRow({ field, texts, changed, binds, serverError, usedToday, tradesToday, lang, onChange, onStep, onBlur, onCap }: Props) {
  const { t } = useTranslation('settings-money')
  const raw = texts[field]
  const clientError = fieldError(field, raw)
  const error = serverError ?? (clientError ? t(`err.${clientError}`) : undefined)
  const sub = useSub(field, texts, usedToday, tradesToday, lang, onCap)
  const label = t(`f.${field}`)
  const id = `ml-${field}`
  const int = isInteger(field)

  return (
    <div className={cx('ml-row', changed && 'is-changed')} data-field={field} data-binding={binds || undefined}>
      <div className="ml-row-main">
        <span className="ml-row-head">
          <label htmlFor={id} className="text-callout">
            {label}
            {changed && <span className="sr-only">, {t('changed')}</span>}
          </label>
          {binds && <BindsBadge />}
        </span>
        <span id={`${id}-sub`} className={cx('ml-sub text-footnote', sub.warn && 'is-warn')}>
          {sub.warn && <TriangleAlert aria-hidden="true" />}
          <span>{sub.node}</span>
        </span>
        {error && (
          <p id={`${id}-err`} className="st-field-error text-footnote" data-testid={`error-${field}`}>
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{error}</span>
          </p>
        )}
      </div>
      <div className={cx('st-stepper ml-stepper', int ? 'is-int' : 'is-usdt', error && 'is-error')}>
        <div className="st-stepper-field" dir="ltr">
          <input
            id={id}
            className={cx('st-stepper-input', int && 'no-unit')}
            inputMode={int ? 'numeric' : 'decimal'}
            autoComplete="off"
            spellCheck={false}
            placeholder={isNullable(field) ? t('ph.noCap') : undefined}
            value={raw}
            aria-invalid={error ? true : undefined}
            aria-describedby={`${id}-sub${error ? ` ${id}-err` : ''}`}
            onChange={(e) => onChange(e.target.value)}
            onBlur={onBlur}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault()
                onStep(e.key === 'ArrowUp' ? 1 : -1)
              }
            }}
          />
          {!int && (
            <span aria-hidden="true" className="st-stepper-unit">
              USDT
            </span>
          )}
        </div>
        <div className="st-stepper-buttons">
          <button type="button" aria-label={t('dec', { label })} disabled={atBound(field, raw, -1)} onClick={() => onStep(-1)}>
            <Minus aria-hidden="true" strokeWidth={2} />
          </button>
          <span className="st-stepper-sep" aria-hidden="true" />
          <button type="button" aria-label={t('inc', { label })} disabled={atBound(field, raw, 1)} onClick={() => onStep(1)}>
            <Plus aria-hidden="true" strokeWidth={2} />
          </button>
        </div>
      </div>
    </div>
  )
}
