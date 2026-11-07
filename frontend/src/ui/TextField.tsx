import { CircleAlert } from 'lucide-react'
import { Label } from 'radix-ui'
import { useId, type InputHTMLAttributes, type ReactNode, type Ref } from 'react'
import { cx } from './cx'

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label: ReactNode
  /** A line under the field explaining the format. Hidden while an error shows. */
  hint?: ReactNode
  /** Shown under the field with an icon; never clears what was typed. */
  error?: ReactNode
  /** Rendered inside the field at the inline end (the SecureField reveal button). */
  endSlot?: ReactNode
  /** Extra classes on the <input> itself (className goes on the wrapper). */
  inputClassName?: string
  ref?: Ref<HTMLInputElement>
}

/** The field box: 40px, radius 10, 1px border; focus turns it accent with the ring. */
export const INPUT_CLASSES =
  'h-10 w-full rounded-control border border-field-border bg-field px-3 text-callout text-label ' +
  'transition-[border-color,box-shadow] duration-[var(--dur-fast)] ' +
  'placeholder:text-label-tertiary hover:border-field-border-hover ' +
  'focus:border-accent focus:shadow-focus focus:outline-none ' +
  'disabled:pointer-events-none disabled:opacity-40 read-only:bg-fill-4 ' +
  'aria-invalid:border-loss aria-invalid:focus:shadow-error-ring'

export function TextField({ label, hint, error, endSlot, inputClassName, id, className, ref, ...input }: TextFieldProps) {
  const autoId = useId()
  const inputId = id ?? `field-${autoId}`
  const messageId = `${inputId}-message`
  const hasError = error !== undefined && error !== null && error !== false
  const message = hasError ? error : hint

  return (
    <div className={cx('grid gap-1.5', className)} data-invalid={hasError || undefined}>
      <Label.Root htmlFor={inputId} className="text-subheadline font-medium text-label">
        {label}
      </Label.Root>
      <div className="relative flex items-center" dir={input.dir}>
        <input
          ref={ref}
          id={inputId}
          aria-invalid={hasError || undefined}
          aria-describedby={message ? messageId : undefined}
          className={cx(INPUT_CLASSES, endSlot !== undefined && 'pe-11', inputClassName)}
          {...input}
        />
        {endSlot !== undefined && <div className="absolute end-1 flex items-center">{endSlot}</div>}
      </div>
      {hasError ? (
        <p id={messageId} className="flex items-start gap-1.5 text-footnote text-loss-ink">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.5} />
          <span>{error}</span>
        </p>
      ) : hint ? (
        <p id={messageId} className="text-footnote text-label-secondary">
          {hint}
        </p>
      ) : null}
    </div>
  )
}
