import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './cx'

export interface GroupedListProps {
  /** A footnote heading above the group. */
  title?: ReactNode
  /** A footnote below the group. */
  note?: ReactNode
  children: ReactNode
  className?: string
}

/**
 * macOS System Settings inset group (design §9): radius 12 on the grouped-row
 * background, a 0.5px hairline, rows separated by 0.5px lines inset 16px
 * (56px when the row has an icon).
 */
export function GroupedList({ title, note, children, className }: GroupedListProps) {
  return (
    <section className={cx('grid', className)}>
      {title && <h3 className="px-4 pb-2 text-footnote font-semibold text-label-secondary">{title}</h3>}
      <div
        role="list"
        className="overflow-hidden rounded-card bg-background-grouped-row shadow-[0_0_0_0.5px_var(--hairline-color)]"
      >
        {children}
      </div>
      {note && <p className="px-4 pt-2 text-footnote text-label-secondary">{note}</p>}
    </section>
  )
}

export interface RowProps {
  /** A 16px icon, shown in a 28px tile at the inline start. */
  icon?: ReactNode
  label: ReactNode
  /** A footnote line under the label. */
  sub?: ReactNode
  /** A value or control at the inline end. */
  children?: ReactNode
  /** Makes the whole row a button (hover fill, focus ring). */
  onClick?: ButtonHTMLAttributes<HTMLButtonElement>['onClick']
  disabled?: boolean
  /** Id for the label, so a control in the row can use aria-labelledby. */
  labelId?: string
  className?: string
}

const SEPARATOR =
  // a 0.5px line above every row but the first
  'relative [&:not(:first-child)]:before:absolute [&:not(:first-child)]:before:top-0 [&:not(:first-child)]:before:end-0 ' +
  "[&:not(:first-child)]:before:border-t-[0.5px] [&:not(:first-child)]:before:border-separator [&:not(:first-child)]:before:content-['']"

const LAYOUT = 'flex min-h-12 w-full items-center gap-3 px-4 py-2.5 text-start'

export function Row({ icon, label, sub, children, onClick, disabled, labelId, className }: RowProps) {
  const inset = icon ? 'before:start-14' : 'before:start-4'
  const body = (
    <>
      {icon && (
        <span
          aria-hidden="true"
          className="grid size-7 shrink-0 place-items-center rounded-[7px] bg-fill-3 text-label-secondary [&_svg]:size-4"
        >
          {icon}
        </span>
      )}
      <span className="grid min-w-0 flex-1 gap-px">
        <span id={labelId} className="text-callout text-label">
          {label}
        </span>
        {sub && <span className="text-footnote text-label-secondary">{sub}</span>}
      </span>
      {children !== undefined && (
        <span className="flex shrink-0 items-center gap-2 text-end text-callout text-label-secondary">{children}</span>
      )}
    </>
  )

  return (
    <div
      role="listitem"
      aria-disabled={(!onClick && disabled) || undefined}
      className={cx(SEPARATOR, inset, disabled && 'opacity-40', className)}
    >
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          disabled={disabled}
          className={cx(LAYOUT, 'hover:bg-fill-4 disabled:pointer-events-none')}
        >
          {body}
        </button>
      ) : (
        <div className={LAYOUT}>{body}</div>
      )}
    </div>
  )
}
