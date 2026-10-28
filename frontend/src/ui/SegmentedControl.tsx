import { MotionConfig, motion } from 'motion/react'
import { RadioGroup } from 'radix-ui'
import { useId, type ReactNode } from 'react'
import { cx } from './cx'
import { SPRING } from './motion'
import { useDir } from './useDir'

export interface SegmentOption<V extends string> {
  value: V
  label: ReactNode
  disabled?: boolean
}

export interface SegmentedControlProps<V extends string> {
  options: ReadonlyArray<SegmentOption<V>>
  value?: V
  defaultValue?: V
  onValueChange?: (value: V) => void
  disabled?: boolean
  /** Required: a radiogroup needs a name ("Range", "Language"). */
  'aria-label': string
  className?: string
}

/**
 * Six options or fewer, for ranges and modes, never for navigation (design §9).
 * A role="radiogroup": arrow keys move and select, skipping disabled segments,
 * and follow the reading direction. The thumb is a motion element with a shared
 * layoutId, so it travels between segments by transform only (FLIP).
 */
export function SegmentedControl<V extends string>({
  options,
  value,
  defaultValue,
  onValueChange,
  disabled,
  className,
  'aria-label': ariaLabel,
}: SegmentedControlProps<V>) {
  const dir = useDir()
  const thumbId = useId()

  return (
    <MotionConfig reducedMotion="user" transition={SPRING}>
      <RadioGroup.Root
        aria-label={ariaLabel}
        value={value}
        defaultValue={defaultValue}
        onValueChange={(next) => onValueChange?.(next as V)}
        disabled={disabled}
        orientation="horizontal"
        dir={dir}
        loop
        className={cx(
          'inline-grid auto-cols-[minmax(44px,auto)] grid-flow-col rounded-[9px] bg-fill-3 p-0.5 isolate',
          disabled && 'opacity-40',
          className,
        )}
      >
        {options.map((option) => (
          <RadioGroup.Item
            key={option.value}
            value={option.value}
            disabled={option.disabled}
            className={cx(
              'group relative h-7 min-w-11 whitespace-nowrap rounded-[7px] px-3 text-footnote font-medium text-label',
              'data-[state=checked]:font-semibold data-[state=unchecked]:hover:bg-fill-4',
              'disabled:pointer-events-none disabled:text-label-tertiary',
            )}
          >
            <RadioGroup.Indicator asChild>
              <motion.span
                layoutId={`segment-thumb-${thumbId}`}
                aria-hidden="true"
                data-segment-thumb=""
                className="absolute inset-0 -z-10 rounded-[7px] bg-segment-thumb shadow-segment-thumb"
              />
            </RadioGroup.Indicator>
            <span className="relative">{option.label}</span>
          </RadioGroup.Item>
        ))}
      </RadioGroup.Root>
    </MotionConfig>
  )
}
