/*
 * The Technical Analyst's TechnicalSetup card (ATL-135, docs/handoff/ATL-135-technical-analyst.md):
 * the `card` of a research_report message with `type: "technical_setup"`. Prices and R values are
 * decimal strings; the reader keeps them as strings so nothing a figure shows passes through a
 * float. Returns null when the card lacks what the renderer needs, and the GenericCard draws it.
 */

export type Bias = 'up' | 'down' | 'range'
export type Direction = 'long' | 'short' | 'none'
export const SETUP_TYPES = [
  'breakout',
  'pullback_to_support',
  'breakdown',
  'rally_to_resistance',
  'range_fade',
  'squeeze_expansion',
  'reversal_divergence',
  'none',
] as const
export type SetupType = (typeof SETUP_TYPES)[number]

/** The timeframes the bias row shows, in this order (brief §7.4 TechnicalSetup.timeframe_bias). */
export const BIAS_TIMEFRAMES = ['1D', '4H', '1H', '15m'] as const

export interface SetupTarget {
  price: string
  /** Code-recomputed R (ATL-135 rule 3), or null when the card has none. */
  r: string | null
  reason: string | null
  /** The share of the position closed at this target, in percent, when the plan says. */
  closePct: string | null
}

export interface SetupLevel {
  id: string
  label: string | null
  price: string | null
  zone: [string, string] | null
}

export interface TechnicalSetup {
  instId: string
  bias: Array<{ tf: string; bias: Bias }>
  setupType: SetupType
  direction: Direction
  /** [low, high] of the entry zone; null on "No setup". */
  entryZone: [string, string] | null
  trigger: string
  stop: string | null
  targets: SetupTarget[]
  expectedR: string | null
  invalidation: string | null
  quality: number | null
  slippageBps: string | null
  levels: SetupLevel[]
  dataAsOf: string
  /** The one condition that would create a setup (setup_type "none"). */
  noSetupReason: string | null
  /** Price now, when the card carries it (design v11 §7 "needs beyond ATL-135"). */
  lastPrice: string | null
  /** Decimals of the instrument's tickSz. */
  tickDecimals: number
  notes: string[]
}

/** Estimated slippage above this many bps shows the thin-liquidity notice (ATL-135 rule 4, brief §7.4 step 6). */
export const THIN_SLIPPAGE_BPS = 10

const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)$/

function dec(v: unknown): string | null {
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  if (typeof v === 'string' && DECIMAL.test(v.trim())) return v.trim()
  return null
}

function text(v: unknown): string | null {
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

function pair(v: unknown): [string, string] | null {
  if (!Array.isArray(v) || v.length !== 2) return null
  const a = dec(v[0])
  const b = dec(v[1])
  if (a === null || b === null) return null
  // Low first, compared as decimals without a float: the reader only orders, never rounds.
  return Number(a) <= Number(b) ? [a, b] : [b, a]
}

/** Fraction digits of a decimal string: "0.10" → 2, "1" → 0. */
export function decimalsOf(v: string): number {
  const cut = v.indexOf('.')
  return cut < 0 ? 0 : v.length - cut - 1
}

/** Fraction digits of a tickSz without trailing zeros: "0.10" → 1, "0.01" → 2, "1" → 0. */
function tickDigits(tick: string): number {
  const cut = tick.indexOf('.')
  if (cut < 0) return 0
  return tick.slice(cut + 1).replace(/0+$/, '').length
}

/**
 * The instrument's tick precision: the card's `tick_decimals`, else the tick size the code's
 * tick_rounding correction records, else the most decimals any price on the card carries.
 */
function tickDecimalsOf(card: Record<string, unknown>, prices: string[]): number {
  const own = card.tick_decimals
  if (typeof own === 'number' && Number.isInteger(own) && own >= 0 && own <= 12) return own
  const meta = card.meta as { corrections?: Array<{ rule?: unknown; tick_sz?: unknown }> } | undefined
  const tick = meta?.corrections?.find((c) => c.rule === 'tick_rounding')?.tick_sz
  const tickDec = dec(tick)
  if (tickDec !== null) return tickDigits(tickDec)
  return prices.reduce((d, p) => Math.max(d, decimalsOf(p)), 0)
}

export function readTechnicalSetup(card: unknown): TechnicalSetup | null {
  if (!card || typeof card !== 'object') return null
  const c = card as Record<string, unknown>
  if (c.type !== 'technical_setup') return null
  const instId = text(c.inst_id)
  const dataAsOf = text(c.data_as_of)
  if (!instId || !dataAsOf || !Number.isFinite(Date.parse(dataAsOf))) return null

  const setupType = (SETUP_TYPES as readonly string[]).includes(String(c.setup_type)) ? (c.setup_type as SetupType) : null
  if (!setupType) return null
  const rawDir = c.direction === 'long' || c.direction === 'short' ? c.direction : 'none'
  const none = setupType === 'none' || rawDir === 'none'

  const rawBias = (c.timeframe_bias ?? {}) as Record<string, unknown>
  const bias = BIAS_TIMEFRAMES.flatMap((tf) => {
    const b = rawBias[tf]
    return b === 'up' || b === 'down' || b === 'range' ? [{ tf, bias: b as Bias }] : []
  })

  const targets: SetupTarget[] = Array.isArray(c.targets)
    ? c.targets.flatMap((x) => {
        if (!x || typeof x !== 'object') return []
        const o = x as Record<string, unknown>
        const price = dec(o.price)
        if (price === null) return []
        return [{ price, r: dec(o.r_multiple ?? o.r), reason: text(o.reason), closePct: dec(o.close_pct) }]
      })
    : []

  const levels: SetupLevel[] = Array.isArray(c.levels_used)
    ? c.levels_used.flatMap((x) => {
        if (!x || typeof x !== 'object') return []
        const o = x as Record<string, unknown>
        const id = text(o.id)
        if (!id) return []
        const zone = pair(o.zone)
        const price = dec(o.price)
        if (!zone && price === null) return []
        return [{ id, label: text(o.label), price, zone }]
      })
    : []

  const entryZone = none ? null : pair(c.entry_zone)
  const stop = none ? null : dec(c.stop)
  // A setup without a zone or a stop cannot be drawn as a plan: show it as the generic card.
  if (!none && (!entryZone || stop === null || targets.length === 0)) return null

  const q = typeof c.quality_score === 'number' && Number.isFinite(c.quality_score) ? Math.max(0, Math.min(1, c.quality_score)) : null
  const lastPrice = dec(c.last_price)
  const prices = [...(entryZone ?? []), ...(stop ? [stop] : []), ...targets.map((x) => x.price)]

  return {
    instId,
    bias,
    setupType: none ? 'none' : setupType,
    direction: none ? 'none' : rawDir,
    entryZone,
    trigger: text(c.trigger) ?? '',
    stop,
    targets: none ? [] : targets,
    expectedR: none ? null : dec(c.expected_r),
    invalidation: text(c.invalidation),
    quality: none ? null : q,
    slippageBps: dec(c.est_slippage_bps),
    levels,
    dataAsOf,
    noSetupReason: text(c.no_setup_reason),
    lastPrice,
    tickDecimals: tickDecimalsOf(c, prices),
    notes: Array.isArray(c.notes) ? c.notes.filter((n): n is string => typeof n === 'string' && n.trim() !== '') : [],
  }
}

/** est_slippage_bps above THIN_SLIPPAGE_BPS (compared as a decimal). */
export function isThin(setup: Pick<TechnicalSetup, 'slippageBps'>): boolean {
  return setup.slippageBps !== null && Number(setup.slippageBps) > THIN_SLIPPAGE_BPS
}
