import { ChartCandlestick, CircleAlert, Minus, Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { Button, Skeleton } from '../../ui'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { EnvScopeControl, type EnvScope } from '../settings-risk/EnvScope'
import { atBound, kindOf, type NumKey } from './model'

/* The Markets page's building blocks (ATL-131): pane header, load states, sections and number rows. */

export function PaneHeader({ version, scope }: { version: number | null; scope: EnvScope }) {
  const { t } = useTranslation('settings-markets')
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <ChartCandlestick strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[60ch] text-subheadline text-label-secondary">{t(`intro.${scope.env}`)}</p>
      </div>
      <div className="lv-aside">
        <EnvScopeControl scope={scope} />
        {version !== null && (
          <span className="st-version" data-testid="settings-version">
            {t('version', { v: formatFixed(version, 0, lang) })}
          </span>
        )}
      </div>
    </header>
  )
}

export function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings-markets')
  const parts = t('error.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="mk-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('error.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code className="font-mono text-footnote">./atlas up</code>
            {parts[1]}
          </>
        ) : (
          t('error.body')
        )}
      </p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('error.retry')}
      </Button>
    </section>
  )
}

export function Loading() {
  return (
    <div className="grid gap-7" aria-busy="true" data-testid="mk-loading">
      {[4, 4, 1].map((n, s) => (
        <div key={s} className="st-group">
          {Array.from({ length: n }, (_, i) => (
            <div key={i} className="ml-row">
              <Skeleton className="h-3.5 w-[36%]" />
              <Skeleton className="h-[30px] w-[168px] rounded-[8px]" />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

export function Section({ id, title, note, children }: { id: string; title: string; note?: string; children: ReactNode }) {
  return (
    <section className="grid" aria-labelledby={id}>
      <h2 id={id} className="ml-group-title">
        {title}
      </h2>
      {children}
      {note && <p className="ml-group-note">{note}</p>}
    </section>
  )
}

export function NumRow({
  field,
  id,
  label,
  ariaLabel,
  sub,
  text,
  error,
  changed,
  dim,
  onChange,
  onStep,
  onBlur,
}: {
  field: NumKey
  id: string
  label: string
  ariaLabel?: string
  sub?: string
  text: string
  error?: string
  changed: boolean
  dim?: boolean
  onChange: (text: string) => void
  onStep: (dir: 1 | -1) => void
  onBlur: () => void
}) {
  const { t } = useTranslation('settings-markets')
  const kind = kindOf(field)
  const unit = kind === 'usd' ? 'USD' : kind === 'bps' ? 'bps' : null
  const name = ariaLabel ?? label
  const describedBy = [sub ? `${id}-sub` : null, error ? `${id}-err` : null].filter(Boolean).join(' ') || undefined
  return (
    <div className={cx('ml-row mk-num', changed && 'is-changed', dim && 'is-dim')} data-field={field}>
      <div className="ml-row-main">
        <span className="ml-row-head">
          <label htmlFor={id} className="text-callout">
            {label}
            {changed && <span className="sr-only">, {t('changed')}</span>}
          </label>
        </span>
        {sub && (
          <span id={`${id}-sub`} className="ml-sub text-footnote">
            <span>{sub}</span>
          </span>
        )}
        {error && (
          <p id={`${id}-err`} className="st-field-error text-footnote" data-testid={`error-${field}`}>
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{error}</span>
          </p>
        )}
      </div>
      <div className={cx('st-stepper ml-stepper mk-stepper', `is-${kind}`, error && 'is-error')}>
        <div className="st-stepper-field" dir="ltr">
          <input
            id={id}
            className={cx('st-stepper-input', !unit && 'no-unit')}
            inputMode={kind === 'int' ? 'numeric' : 'decimal'}
            autoComplete="off"
            spellCheck={false}
            value={text}
            aria-label={ariaLabel}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            onChange={(e) => onChange(e.target.value)}
            onBlur={onBlur}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault()
                onStep(e.key === 'ArrowUp' ? 1 : -1)
              }
            }}
          />
          {unit && (
            <span aria-hidden="true" className="st-stepper-unit">
              {unit}
            </span>
          )}
        </div>
        <div className="st-stepper-buttons">
          <button type="button" aria-label={t('dec', { label: name })} disabled={atBound(field, text, -1)} onClick={() => onStep(-1)}>
            <Minus aria-hidden="true" strokeWidth={2} />
          </button>
          <span className="st-stepper-sep" aria-hidden="true" />
          <button type="button" aria-label={t('inc', { label: name })} disabled={atBound(field, text, 1)} onClick={() => onStep(1)}>
            <Plus aria-hidden="true" strokeWidth={2} />
          </button>
        </div>
      </div>
    </div>
  )
}
