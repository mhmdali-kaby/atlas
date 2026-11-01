import { formatDecimal, type Lang } from '../../lib/format'

/* Column lists and text helpers of Portfolio → Orders (ATL-150), shared by its tables and Cancel sheet. */

export const OPEN_COLUMNS = ['instrument', 'side', 'type', 'price', 'size', 'filled', 'state', 'origin', 'age'] as const
export const OPEN_NUMERIC = new Set<string>(['price', 'size', 'filled', 'age'])
export const ALGO_COLUMNS = ['instrument', 'kind', 'trigger', 'size', 'state'] as const
export const ALGO_NUMERIC = new Set<string>(['trigger', 'size'])
const KNOWN_TYPES = new Set(['limit', 'market', 'post_only', 'fok', 'ioc'])

/** Contracts: every digit the API sent, no forced decimals ("12.02", "2"). */
export const sizeText = (v: string | null, lang: Lang) => formatDecimal(v, lang, 0)

/** The short form of a proposal UUID shown in the Origin cell. */
export const shortId = (id: string) => id.slice(0, 8)

export function typeText(t: (k: string) => string, ordType: string) {
  return KNOWN_TYPES.has(ordType) ? t(`type.${ordType}`) : ordType
}

