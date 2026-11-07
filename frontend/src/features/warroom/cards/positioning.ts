/*
 * The Derivatives & Sentiment Analyst's PositioningReport card (ATL-136,
 * docs/handoff/ATL-136-derivatives-agent.md): the `card` of a research_report message with
 * `type: "positioning_report"`. Decimal fields (basis_bps, underlying_premium_pct) stay strings so
 * nothing they show passes through a float. Returns null when the card lacks what the renderer
 * needs, and the GenericCard draws it.
 */

export const REGIMES = ['new_longs', 'short_covering', 'new_shorts', 'long_liquidation', 'flat'] as const
export type Regime = (typeof REGIMES)[number]
export type Stance = 'supports' | 'neutral' | 'opposes'
export type CrowdedSide = 'long' | 'short' | 'none'

export interface Evidence {
  text: string
  /** ISO time the number was read at, when the line carries one. */
  asOf: string | null
}

export interface PositioningReport {
  instId: string
  regime: Regime
  /** The card's own one-line meaning, when it carries one; else the UI says it per regime. */
  regimeMeaning: string | null
  crowding: number
  crowdedSide: CrowdedSide
  longSqueeze: number
  shortSqueeze: number
  fundingView: string
  basisBps: string | null
  underlyingPremiumPct: string | null
  sentiment: number
  supports: { long: Stance; short: Stance }
  evidence: Evidence[]
  dataAsOf: string
  reliable: boolean
  /** The code-written sentences saying why the data is unreliable (empty when reliable). */
  unreliableReasons: string[]
}

const STANCES: readonly Stance[] = ['supports', 'neutral', 'opposes']
const SIDES: readonly CrowdedSide[] = ['long', 'short', 'none']

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const num = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
  return Number.isFinite(n) ? n : null
}
const clamp01 = (n: number) => Math.min(1, Math.max(0, n))
const dec = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v.trim() : typeof v === 'number' && Number.isFinite(v) ? String(v) : null)

/** ATL-136 writes "OI −6.0% in 24h with price +3.0% (as_of 2026-10-30T10:00Z)"; the design wants {text, as_of}. */
const AS_OF_TAIL = /\s*\(\s*as[_ ]of[:\s]+([^)]+?)\s*\)\s*\.?\s*$/i

export function readEvidence(v: unknown): Evidence | null {
  if (isObj(v) && typeof v.text === 'string' && v.text.trim() !== '') {
    const at = typeof v.as_of === 'string' && !Number.isNaN(Date.parse(v.as_of)) ? v.as_of : null
    return { text: v.text.trim(), asOf: at }
  }
  if (typeof v !== 'string' || v.trim() === '') return null
  const m = AS_OF_TAIL.exec(v)
  if (m && !Number.isNaN(Date.parse(m[1]!))) return { text: v.slice(0, m.index).trim(), asOf: m[1]! }
  return { text: v.trim(), asOf: null }
}

export function readPositioningReport(card: unknown): PositioningReport | null {
  if (!isObj(card) || card.type !== 'positioning_report') return null
  if (typeof card.inst_id !== 'string' || typeof card.data_as_of !== 'string') return null
  const regime = (REGIMES as readonly string[]).includes(card.regime as string) ? (card.regime as Regime) : null
  const sd = card.supports_direction
  if (!regime || !isObj(sd)) return null
  const stance = (v: unknown): Stance => (STANCES.includes(v as Stance) ? (v as Stance) : 'neutral')
  const sq = isObj(card.squeeze_risk) ? card.squeeze_risk : {}
  // ATL-136 writes data_reliable; the design's mock says reliable.
  const reliable = card.data_reliable !== false && card.reliable !== false
  const reasons: string[] = []
  if (isObj(card.unreliable_reasons)) {
    for (const r of Object.values(card.unreliable_reasons)) if (typeof r === 'string' && r.trim() !== '') reasons.push(r.trim())
  }
  if (typeof card.unreliable_reason === 'string' && card.unreliable_reason.trim() !== '') reasons.push(card.unreliable_reason.trim())
  return {
    instId: card.inst_id,
    regime,
    regimeMeaning: typeof card.regime_meaning === 'string' && card.regime_meaning.trim() !== '' ? card.regime_meaning.trim() : null,
    crowding: clamp01(num(card.crowding_score) ?? 0),
    crowdedSide: SIDES.includes(card.crowded_side as CrowdedSide) ? (card.crowded_side as CrowdedSide) : 'none',
    longSqueeze: clamp01(num(sq.long_squeeze) ?? 0),
    shortSqueeze: clamp01(num(sq.short_squeeze) ?? 0),
    fundingView: typeof card.funding_view === 'string' ? card.funding_view.trim() : '',
    basisBps: dec(card.basis_bps),
    underlyingPremiumPct: dec(card.underlying_premium_pct),
    sentiment: Math.min(1, Math.max(-1, num(card.sentiment_score) ?? 0)),
    // Unreliable data is never a direction (ATL-136 unreliable_data_neutral), whatever the card says.
    supports: reliable ? { long: stance(sd.long), short: stance(sd.short) } : { long: 'neutral', short: 'neutral' },
    evidence: Array.isArray(card.key_evidence) ? card.key_evidence.map(readEvidence).filter((e): e is Evidence => e !== null) : [],
    dataAsOf: card.data_as_of,
    reliable,
    unreliableReasons: reasons,
  }
}
