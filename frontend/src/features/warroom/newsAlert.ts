import type { WarroomMessage } from './api'

/*
 * Reads a `news_alert` message's card: ATL-65's news.alert payload, stored verbatim by the bridge
 * (docs/handoff/ATL-65-news-alerts.md). Every field is untrusted and checked here, so the card
 * never draws NaN, an empty chip or a non-http(s) link.
 */

export interface NewsAlert {
  headline: string
  sourceName: string
  url: URL | null
  publishedAt: string | null
  importance: number
  eventType: string | null
  instIds: string[]
  /** −1…1, or null when the item was not scored (or the value is not a finite number). */
  sentiment: number | null
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)

function httpUrl(v: unknown): URL | null {
  const s = str(v)
  if (!s) return null
  try {
    const u = new URL(s)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u : null
  } catch {
    return null
  }
}

/** The card's fields, or null when the message carries no headline (GenericCard draws it then). */
export function readNewsAlert(card: WarroomMessage['card']): NewsAlert | null {
  if (!card) return null
  const headline = str(card.headline)
  if (!headline) return null
  const imp = typeof card.importance === 'number' && Number.isFinite(card.importance) ? Math.round(card.importance) : 1
  const sentiment = typeof card.sentiment === 'number' && Number.isFinite(card.sentiment) ? Math.max(-1, Math.min(1, card.sentiment)) : null
  const ids = Array.isArray(card.inst_ids) ? card.inst_ids : []
  const published = str(card.published_at)
  return {
    headline,
    sourceName: str(card.source_name) ?? str(card.source) ?? '',
    url: httpUrl(card.url),
    publishedAt: published && !Number.isNaN(Date.parse(published)) ? published : null,
    importance: Math.max(1, Math.min(5, imp)),
    eventType: str(card.event_type),
    instIds: [...new Set(ids.filter((id): id is string => typeof id === 'string' && id.trim() !== ''))],
    sentiment,
  }
}
