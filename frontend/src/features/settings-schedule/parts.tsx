import { CircleAlert, ChevronsUpDown } from 'lucide-react'
import type { ReactNode } from 'react'
import type { Lang } from '../../lib/format'
import { cx } from '../../ui/cx'
import { WEEKDAYS, type Weekday } from './model'
import { dayName } from './words'

export function Fig({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={cx('tabular-nums', className)}>
      {children}
    </bdi>
  )
}

export function FieldError({ id, message }: { id: string; message?: string | undefined }) {
  if (!message) return null
  return (
    <p id={id} className="st-field-error sch-err text-footnote" data-testid={id}>
      <CircleAlert aria-hidden="true" strokeWidth={1.75} />
      <span>{message}</span>
    </p>
  )
}

interface TimeInputProps {
  id: string
  label: string
  value: string
  error?: string | undefined
  alt?: string | undefined
  onText: (text: string) => void
  onCommit: () => void
  onNudge: (minutes: number) => void
}

/** A 24-hour Baghdad time in figures, with the owner-zone moment under it when the zones differ. */
export function TimeInput({ id, label, value, error, alt, onText, onCommit, onNudge }: TimeInputProps) {
  return (
    <span className={cx('sch-tf', error && 'is-error')}>
      <input
        id={id}
        className="sch-time"
        value={value}
        inputMode="numeric"
        maxLength={5}
        autoComplete="off"
        spellCheck={false}
        dir="ltr"
        placeholder="00:00"
        aria-label={label}
        aria-invalid={error ? true : undefined}
        aria-describedby={[alt ? `${id}-alt` : '', error ? `${id}-err` : ''].filter(Boolean).join(' ') || undefined}
        onChange={(e) => onText(e.target.value)}
        onBlur={onCommit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onCommit()
          if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
          e.preventDefault()
          onNudge((e.shiftKey ? 60 : 5) * (e.key === 'ArrowUp' ? 1 : -1))
        }}
      />
      {alt && (
        <span className="sch-alt" id={`${id}-alt`} data-testid={`${id}-alt`}>
          {alt}
        </span>
      )}
    </span>
  )
}

export function DaySelect({ id, label, value, lang, onChange }: { id: string; label: string; value: Weekday; lang: Lang; onChange: (d: Weekday) => void }) {
  return (
    <label className="sch-day">
      <span className="sr-only">{label}</span>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as Weekday)}>
        {WEEKDAYS.map((w) => (
          <option key={w} value={w}>
            {dayName(w, lang)}
          </option>
        ))}
      </select>
      <ChevronsUpDown aria-hidden="true" className="sch-day-caret" strokeWidth={1.75} />
    </label>
  )
}
