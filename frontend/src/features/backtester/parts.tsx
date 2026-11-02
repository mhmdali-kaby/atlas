import { CircleAlert, CircleCheck, CircleX, Minus, Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import type { RunStatus } from './api'

export const NS = 'backtester'

/** A figure or identifier as an LTR island with tabular numerals (design §12.1). */
export function Fig({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={cx('fig tabular-nums', className)}>
      {children}
    </bdi>
  )
}

export function FieldError({ id, children, className }: { id: string; children: ReactNode; className?: string }) {
  return (
    <p id={id} className={cx('bt-field-error', className)} data-testid={id}>
      <CircleAlert aria-hidden="true" strokeWidth={1.75} />
      <span>{children}</span>
    </p>
  )
}

/** The design's status pill: Done (green), Failed (red), Running/Queued (accent, with the spinner). */
export function StatusChip({ status }: { status: RunStatus }) {
  const { t } = useTranslation(NS)
  const kind = status === 'done' ? 'is-done' : status === 'failed' ? 'is-failed' : 'is-running'
  return (
    <span className={cx('bt-status', kind)} data-testid="bt-status" data-status={status}>
      {status === 'done' ? (
        <CircleCheck aria-hidden="true" />
      ) : status === 'failed' ? (
        <CircleX aria-hidden="true" />
      ) : (
        <Spinner />
      )}
      <span>{t(`st.${status}`)}</span>
    </span>
  )
}

interface StepperProps {
  /** What the buttons change, for their names ("Decrease Window"). */
  label: string
  value: ReactNode
  canDec: boolean
  canInc: boolean
  onStep: (dir: 1 | -1) => void
  children?: ReactNode
}

/** The design's compact stepper (.bt-step): − value +. */
export function Stepper({ label, value, canDec, canInc, onStep, children }: StepperProps) {
  const { t } = useTranslation(NS)
  return (
    <div className="bt-step">
      <button type="button" className="bt-icon-btn" aria-label={t('f.dec', { what: label })} disabled={!canDec} onClick={() => onStep(-1)}>
        <Minus aria-hidden="true" strokeWidth={2} />
      </button>
      {children ?? <span className="bt-step-v">{value}</span>}
      <button type="button" className="bt-icon-btn" aria-label={t('f.inc', { what: label })} disabled={!canInc} onClick={() => onStep(1)}>
        <Plus aria-hidden="true" strokeWidth={2} />
      </button>
    </div>
  )
}
