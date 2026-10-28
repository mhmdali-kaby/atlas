import { Slot } from 'radix-ui'
import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from './cx'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'secondary' | 'destructive' | 'plain' | 'plain-destructive'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** 16px icon before the label (Lucide, 1.5 stroke). */
  icon?: ReactNode
  /** Keeps the label, adds the spinner and blocks clicks (design §8). */
  loading?: boolean
  /** Render the child element (a link) with the button's look. */
  asChild?: boolean
  ref?: Ref<HTMLButtonElement>
}

const BASE =
  'inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap font-semibold leading-none ' +
  'transition-[background-color,transform,box-shadow,opacity] duration-[var(--dur-fast)] ease-[var(--ease-out)] ' +
  'active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 aria-disabled:pointer-events-none aria-disabled:opacity-40 ' +
  '[&_svg]:size-4 [&_svg]:shrink-0'

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 rounded-[8px] px-2.5 text-footnote',
  md: 'h-8 rounded-control px-3.5 text-subheadline',
  lg: 'h-11 rounded-control px-5 text-body',
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent-solid text-on-solid shadow-1 hover:bg-accent-solid-hover active:bg-accent-solid-active',
  secondary: 'bg-fill-2 text-label hover:bg-fill-1',
  destructive: 'bg-destructive-solid text-on-solid shadow-1 hover:brightness-[1.06]',
  plain: 'bg-transparent text-accent-ink hover:bg-fill-4',
  'plain-destructive': 'bg-transparent text-loss-ink hover:bg-loss-wash',
}

/** Plain buttons are tighter than filled ones (design: 8px inline padding). */
const PLAIN_PADDING: Partial<Record<ButtonSize, string>> = { md: 'px-2', lg: 'px-3' }

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  loading = false,
  asChild = false,
  disabled,
  type,
  className,
  children,
  ref,
  ...rest
}: ButtonProps) {
  const { t } = useTranslation('ui')
  const plain = variant === 'plain' || variant === 'plain-destructive'
  const classes = cx(BASE, SIZES[size], VARIANTS[variant], plain && PLAIN_PADDING[size], loading && 'pointer-events-none', className)

  if (asChild) {
    return (
      <Slot.Root ref={ref} className={classes} aria-disabled={disabled || undefined} {...rest}>
        {children}
      </Slot.Root>
    )
  }

  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      disabled={disabled}
      aria-busy={loading || undefined}
      className={classes}
      {...rest}
    >
      {loading ? <Spinner /> : icon}
      <span className={cx(loading && 'opacity-75')}>{children}</span>
      {loading && <span className="sr-only">{t('loading')}</span>}
    </button>
  )
}
