import { Switch } from 'radix-ui'
import type { ComponentProps } from 'react'
import { cx } from './cx'

export type ToggleProps = Omit<ComponentProps<typeof Switch.Root>, 'children' | 'asChild'>

/**
 * iOS/macOS-style switch, 42×26 (design §9). Accent when on (macOS tints
 * switches with the accent); --switch-off with a 1px border when off. The knob
 * slides by transform on the --ease-settle spring and mirrors in RTL. Use it
 * only for settings that apply instantly; give it aria-label or
 * aria-labelledby (usually the Row's label).
 */
export function Toggle({ className, ...props }: ToggleProps) {
  return (
    <Switch.Root
      {...props}
      className={cx(
        'relative inline-flex h-[26px] w-[42px] shrink-0 items-center rounded-full bg-switch-off',
        'shadow-[inset_0_0_0_1px_var(--switch-off-border)] transition-colors duration-[var(--dur-base)] ease-[var(--ease-out)]',
        'data-[state=checked]:bg-accent data-[state=checked]:shadow-none',
        'disabled:pointer-events-none disabled:opacity-40',
        'focus-visible:shadow-focus data-[state=checked]:focus-visible:shadow-focus',
        className,
      )}
    >
      <Switch.Thumb
        className={cx(
          'pointer-events-none block size-[22px] translate-x-0.5 rounded-full bg-knob shadow-knob',
          'transition-transform duration-[var(--dur-slow)] ease-[var(--ease-settle)]',
          'data-[state=checked]:translate-x-[18px]',
          'rtl:-translate-x-0.5 rtl:data-[state=checked]:-translate-x-[18px]',
        )}
      />
    </Switch.Root>
  )
}
