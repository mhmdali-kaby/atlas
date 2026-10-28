import type { Transition } from 'motion/react'

/*
 * The one spring (design §7): stiffness 300, damping 30, mass 1. It settles
 * without a visible overshoot. Stiffer springs (350, 400) overshoot by up to 3%
 * and read as a bounce, which the design rules out for data and controls.
 * CSS uses the same curve sampled as `ease-settle`.
 *
 * Every component that animates with motion wraps itself in
 * <MotionConfig reducedMotion="user">, so prefers-reduced-motion turns
 * transforms off; the CSS base layer does the same for CSS transitions.
 */
export const SPRING: Transition = { type: 'spring', stiffness: 300, damping: 30, mass: 1 }

/** Fades: --dur-base with --ease-out. */
export const FADE: Transition = { duration: 0.25, ease: [0.22, 1, 0.36, 1] }
