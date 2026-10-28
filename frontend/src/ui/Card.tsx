import type { HTMLAttributes, Ref } from 'react'
import { cx } from './cx'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  ref?: Ref<HTMLDivElement>
}

/**
 * Radius 12, padding 20, tertiary background, hairline plus shadow-1
 * (design §9). Only to lift content off a busy ground; never a grid of tiles.
 */
export function Card({ className, ref, ...rest }: CardProps) {
  return (
    <div
      ref={ref}
      className={cx(
        'rounded-card bg-background-tertiary p-5 text-label shadow-[0_0_0_0.5px_var(--hairline-color),var(--ui-shadow-1)]',
        className,
      )}
      {...rest}
    />
  )
}
