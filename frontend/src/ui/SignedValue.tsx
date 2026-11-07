import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatSigned, NO_VALUE, signOf, type Figure, type FigureUnit, type Lang } from '../lib/format'
import { cx } from './cx'

export interface SignedValueProps {
  value: Figure
  /** An ISO currency code ("USD") or 'percent' (value already in percent units). */
  unit: FigureUnit
  /** Shown as a tooltip on "—" and read out instead of the dash. Makes it focusable. */
  nullTip?: string
  className?: string
}

/**
 * PnL and funding only: values that can take either sign (design §9).
 * Colour is never the only signal: a gain is green with "+", an up-right arrow
 * and the words "gain"; a loss is red with "−", a down-right arrow and "loss".
 * Arrows mirror in RTL. Zero has no sign, no icon, the label colour. A missing
 * value is "—" in the secondary label with no sign and no icon.
 */
export function SignedValue({ value, unit, nullTip, className }: SignedValueProps) {
  const { t, i18n } = useTranslation('ui')
  const lang: Lang = i18n.language === 'ar' ? 'ar' : 'en'
  const sign = signOf(value)

  if (sign === null) {
    return (
      <span
        data-sign="none"
        className={cx('fig whitespace-nowrap text-label-secondary', nullTip && 'cursor-help', className)}
        tabIndex={nullTip ? 0 : undefined}
        title={nullTip}
        aria-label={nullTip}
        role={nullTip ? 'note' : undefined}
      >
        {NO_VALUE}
      </span>
    )
  }

  const text = formatSigned(value, unit, lang)
  const tone = sign > 0 ? 'profit' : sign < 0 ? 'loss' : 'zero'
  const Icon = sign > 0 ? ArrowUpRight : ArrowDownRight

  return (
    <span
      data-sign={tone}
      className={cx(
        'inline-flex items-center gap-[3px] whitespace-nowrap',
        tone === 'profit' && 'text-profit-ink',
        tone === 'loss' && 'text-loss-ink',
        tone === 'zero' && 'text-label',
        className,
      )}
    >
      {sign !== 0 && (
        <Icon aria-hidden="true" data-sign-icon="" className="size-[0.9em] shrink-0 rtl:-scale-x-100" strokeWidth={2.25} />
      )}
      <span className="fig">{text}</span>
      {sign !== 0 && <span className="sr-only">{sign > 0 ? t('signed.gain') : t('signed.loss')}</span>}
    </span>
  )
}
