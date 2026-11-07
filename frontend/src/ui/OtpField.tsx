import { CircleAlert } from 'lucide-react'
import { useEffect, useRef, useState, type Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from './cx'

export const OTP_LENGTH = 6

export interface OtpFieldProps {
  id: string
  value: string
  onChange: (value: string) => void
  /** Called once each time the field reaches six digits (auto-submit). */
  onComplete?: (value: string) => void
  /** `lg` on the login, `md` in sheets (design v22 §2). */
  size?: 'md' | 'lg'
  error?: string
  /** Bump to shake the cells once (a wrong code). */
  shakeKey?: number
  disabled?: boolean
  autoFocus?: boolean
  /** Extra ids for aria-describedby (the dial's words). */
  describedBy?: string
  ref?: Ref<HTMLInputElement>
}

/**
 * The 6-digit code field (design v22 §5.1): one real input, opacity 0, lying over six drawn
 * cells, so paste, autofill and "From Messages" all land in one place. Non-digits are dropped
 * ("482 913" → 482913). The cells read left to right in Arabic too.
 */
export function OtpField({
  id,
  value,
  onChange,
  onComplete,
  size = 'md',
  error,
  shakeKey = 0,
  disabled,
  autoFocus,
  describedBy,
  ref,
}: OtpFieldProps) {
  const { t } = useTranslation('ui')
  const [focused, setFocused] = useState(false)
  const [shaking, setShaking] = useState(false)
  const lastShake = useRef(shakeKey)

  useEffect(() => {
    if (shakeKey === lastShake.current) return
    lastShake.current = shakeKey
    setShaking(true)
    const timer = setTimeout(() => setShaking(false), 400)
    return () => clearTimeout(timer)
  }, [shakeKey])

  const errorId = `${id}-err`
  const cell = (i: number) => {
    const active = focused && (i === value.length || (value.length === OTP_LENGTH && i === OTP_LENGTH - 1))
    return (
      <span key={i} className={cx('otp-cell', value[i] && 'is-filled', active && 'is-active')}>
        <span className="otp-digit">{value[i] ?? ''}</span>
        <span className="otp-caret" />
      </span>
    )
  }

  return (
    <div className="grid justify-items-center gap-2">
      <div
        className={cx('otp', size === 'lg' && 'is-lg', error && 'is-error', shaking && 'is-shaking')}
        data-testid={`${id}-cells`}
      >
        <input
          ref={ref}
          id={id}
          name="code"
          className="otp-input"
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={OTP_LENGTH + 1}
          autoComplete="one-time-code"
          autoCorrect="off"
          spellCheck={false}
          dir="ltr"
          autoFocus={autoFocus}
          aria-label={t('otp.label')}
          aria-invalid={error ? true : undefined}
          aria-describedby={[error ? errorId : null, describedBy].filter(Boolean).join(' ') || undefined}
          disabled={disabled}
          value={value}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={(e) => {
            const clean = e.target.value.replace(/\D/g, '').slice(0, OTP_LENGTH)
            if (clean === value) return
            onChange(clean)
            if (clean.length === OTP_LENGTH) onComplete?.(clean)
          }}
          onSelect={(e) => {
            // The caret always sits at the end; arrows and clicks don't move it into the middle.
            const el = e.currentTarget
            const n = el.value.length
            if (el.selectionStart !== n || el.selectionEnd !== n) el.setSelectionRange(n, n)
          }}
        />
        <span className="otp-cells" aria-hidden="true">
          {cell(0)}
          {cell(1)}
          {cell(2)}
          <span className="otp-gap" />
          {cell(3)}
          {cell(4)}
          {cell(5)}
        </span>
      </div>
      {error && (
        <p id={errorId} role="alert" className="flex items-start justify-center gap-1.5 text-footnote text-loss-ink">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.5} />
          <span>{error}</span>
        </p>
      )}
    </div>
  )
}

/**
 * The 30-second ring, phased to UTC like every authenticator app (TOTP steps start on whole
 * 30-second marks). Decorative: the words beside it carry the meaning. Hidden under reduced motion.
 */
export function OtpDial() {
  const [at] = useState(() => (Date.now() / 1000) % 30)
  return (
    <span className="otp-dial" aria-hidden="true" style={{ ['--dial-at' as string]: `-${at.toFixed(2)}s` }}>
      <svg viewBox="0 0 20 20">
        <circle className="otp-dial-track" cx="10" cy="10" r="8" />
        <circle className="otp-dial-arc" cx="10" cy="10" r="8" pathLength={100} />
      </svg>
    </span>
  )
}
