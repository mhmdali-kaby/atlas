/* Small display helpers for Portfolio → Performance (ATL-175). */

const LRI = '\u2066'
const PDI = '\u2069'

/** A figure as a plain-text left-to-right isolate, for aria-labels, tooltips and sentences (design Phase 2 §12.1). */
export const iso = (s: string) => `${LRI}${s}${PDI}`

const STEPS = 4

/** The heatmap's step, 1..4 per side, scaled to the largest |P&L| cell; 0 for a cell that nets to zero. */
export function heatStep(v: number, maxAbs: number): number {
  if (Math.abs(v) < 0.005 || maxAbs <= 0) return 0
  return Math.min(STEPS, Math.max(1, Math.ceil((Math.abs(v) / maxAbs) * STEPS)))
}
