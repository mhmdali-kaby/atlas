import type { CSSProperties } from 'react'
import { cx } from './cx'

/** A loading placeholder: a shimmering fill-3/fill-4 block (design: skeletons). Decorative. */
export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return <span aria-hidden="true" className={cx('ui-skeleton', className)} style={style} />
}
