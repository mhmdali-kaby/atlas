import type { LucideIcon } from 'lucide-react'
import type { ReactNode, Ref } from 'react'
import { cx } from '../../ui/cx'

export type NoticeTone = 'info' | 'success' | 'warning' | 'error'

const TONE: Record<NoticeTone, { box: string; icon: string }> = {
  info: { box: 'bg-fill-4', icon: 'text-accent' },
  success: { box: 'bg-profit-wash', icon: 'text-profit-ink' },
  warning: { box: 'bg-warning-wash', icon: 'text-warning-ink' },
  error: { box: 'bg-loss-wash', icon: 'text-loss-ink' },
}

/**
 * Design §9 Notice: an inline banner, radius 12, a wash for success/warning/error and a neutral
 * fill for info; a 20px icon, a subheadline-600 title, a footnote body and optional actions.
 */
export function Notice({
  tone,
  icon: Icon,
  title,
  children,
  actions,
  role,
  className,
  ref,
  ...rest
}: {
  tone: NoticeTone
  icon: LucideIcon
  title: ReactNode
  children?: ReactNode
  actions?: ReactNode
  role?: 'alert' | 'status'
  className?: string
  ref?: Ref<HTMLDivElement>
  'data-testid'?: string
  tabIndex?: number
}) {
  const tone_ = TONE[tone]
  return (
    <div
      ref={ref}
      role={role}
      data-tone={tone}
      className={cx(
        'flex items-start gap-3 rounded-card px-4 py-3.5 shadow-[inset_0_0_0_0.5px_var(--hairline-color)] focus:outline-none',
        tone_.box,
        className,
      )}
      {...rest}
    >
      <Icon aria-hidden="true" strokeWidth={1.5} className={cx('mt-px size-5 shrink-0', tone_.icon)} />
      <div className="min-w-0">
        <p className="text-subheadline font-semibold text-label">{title}</p>
        {children !== undefined && <p className="mt-0.5 text-footnote text-label-secondary">{children}</p>}
        {actions !== undefined && <div className="mt-2.5 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  )
}
