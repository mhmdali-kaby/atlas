/*
 * The 11 brief §17 notification events (ATL-214): one id, group and tone each. This is the single
 * catalogue: Settings → Notifications (ATL-218) groups its switches by it, and the toolbar list,
 * toasts and browser banners (ATL-217) add an icon and an action per id on top of it.
 */

export const GROUPS = ['needs', 'trades', 'team'] as const
export type Group = (typeof GROUPS)[number]

export const EVENTS = [
  { id: 'proposal_awaiting_approval', group: 'needs', tone: 'accent' },
  { id: 'circuit_breaker', group: 'needs', tone: 'critical' },
  { id: 'kill_switch', group: 'needs', tone: 'critical' },
  { id: 'reconciliation_mismatch', group: 'needs', tone: 'critical' },
  { id: 'order_filled', group: 'trades', tone: 'neutral' },
  { id: 'stop_hit', group: 'trades', tone: 'warning' },
  { id: 'target_hit', group: 'trades', tone: 'profit' },
  { id: 'news_alert', group: 'team', tone: 'warning' },
  { id: 'agent_degraded', group: 'team', tone: 'warning' },
  { id: 'data_stale', group: 'team', tone: 'warning' },
  { id: 'daily_report', group: 'team', tone: 'neutral' },
] as const satisfies readonly { id: string; group: Group; tone: string }[]

export type EventId = (typeof EVENTS)[number]['id']
export type Tone = (typeof EVENTS)[number]['tone']

const TONES: Record<string, Tone> = Object.fromEntries(EVENTS.map((e) => [e.id, e.tone]))

export function isEventId(v: string): v is EventId {
  return v in TONES
}

/** The event's tone; an event this build does not know reads as neutral. */
export const toneOf = (event: string): Tone => TONES[event] ?? 'neutral'

/** An LTR isolate inside running text (figures, times, IDs; design §7). */
export const iso = (s: string) => `⁦${s}⁩`
