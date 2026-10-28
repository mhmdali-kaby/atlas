import { cx } from './cx'

const SPOKES = Array.from({ length: 12 }, (_, i) => i)

/**
 * Apple's 12-spoke activity indicator (design §9), in currentColor. Decorative:
 * the control that shows it carries aria-busy and its own label.
 */
export function Spinner({ size = 16, className }: { size?: 16 | 28; className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-spinner=""
      className={cx('relative inline-block shrink-0', className)}
      style={{ width: size, height: size }}
    >
      {SPOKES.map((i) => (
        <i
          key={i}
          className="atlas-spoke absolute start-[46%] top-0 h-[28%] w-[8%] rounded-[2px] bg-current"
          style={{ transform: `rotate(${i * 30}deg)`, animationDelay: `${(i - 12) / 12}s` }}
        />
      ))}
    </span>
  )
}
