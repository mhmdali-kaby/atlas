import { FIGURE_LOCALE, type Lang } from '../../lib/format'
import type { AgentStatus } from '../warroom/agents'
import type { AgentRow, ClaudeStatus } from './api'

/*
 * What the Agents page shows, worked out from the API rows (design v9 agents.js). Pure, so the
 * vitest suite checks every rule without rendering.
 */

/** The state the page shows: the API's, or "over_budget" once either daily budget is reached. */
export type ShownState = 'idle' | 'thinking' | 'waiting' | 'degraded' | 'rate_limited' | 'paused' | 'disabled' | 'over_budget'

const KNOWN: ReadonlySet<string> = new Set(['idle', 'thinking', 'waiting', 'degraded', 'rate_limited', 'paused', 'disabled'])

/** Usage reached either daily budget: "Budget used" whatever the stored state, until Baghdad midnight. */
export function isOverBudget(d: AgentRow): boolean {
  return d.enabled && (d.today.tokens >= d.budget.daily_tokens || d.today.turns >= d.budget.daily_turns)
}

export function shownState(d: AgentRow): ShownState {
  if (!d.enabled || d.state === 'disabled') return 'disabled'
  if (isOverBudget(d)) return 'over_budget'
  return KNOWN.has(d.state) ? (d.state as ShownState) : 'idle'
}

/** The dot and medallion status for a shown state (the War Room's five-colour set). */
export function medalStatus(st: ShownState): AgentStatus {
  switch (st) {
    case 'disabled':
      return 'offline'
    case 'rate_limited':
    case 'over_budget':
      return 'degraded'
    default:
      return st
  }
}

/** States that read as trouble (orange word). */
export const isWarnState = (st: ShownState) => st === 'degraded' || st === 'rate_limited' || st === 'over_budget'

/** The design's warning threshold: a meter at or above 80% of its budget turns orange. */
export const WARN_PCT = 80

export interface Meter {
  used: number
  budget: number
  /** 0–100, rounded, capped at 100. */
  pct: number
  warn: boolean
  full: boolean
  /** Bar width in %: at least 2 when anything was used, so a little use still shows. */
  fill: number
}

export function meter(used: number, budget: number): Meter {
  const pct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0
  return { used, budget, pct, warn: pct >= WARN_PCT, full: used >= budget, fill: Math.max(used > 0 ? 2 : 0, pct) }
}

/** "1.28M", "620K", "3M", "12.4K", "950" — token counts, Western digits in both languages. */
export function compact(n: number, lang: Lang): string {
  const trim = (s: string) => s.replace(/\.?0+$/, '')
  if (n >= 1e6) {
    const v = n / 1e6
    return `${trim(v >= 10 ? v.toFixed(1) : v.toFixed(2))}M`
  }
  if (n >= 1e3) {
    const v = n / 1e3
    return `${(v >= 100 ? v.toFixed(0) : v.toFixed(1)).replace(/\.0$/, '')}K`
  }
  return new Intl.NumberFormat(FIGURE_LOCALE[lang], { maximumFractionDigits: 0 }).format(n)
}

/** The sum of the agents' notional costs, added as decimal strings in micro-dollars (no float sums). */
export function sumNotional(values: readonly string[]): string {
  let micros = 0n
  for (const v of values) {
    const m = /^(-?)(\d+)(?:\.(\d{0,6}))?/.exec(v.trim())
    if (!m) continue
    const whole = BigInt(m[2] ?? '0') * 1_000_000n + BigInt((m[3] ?? '').padEnd(6, '0') || '0')
    micros += m[1] === '-' ? -whole : whole
  }
  const neg = micros < 0n
  const abs = neg ? -micros : micros
  return `${neg ? '-' : ''}${abs / 1_000_000n}.${(abs % 1_000_000n).toString().padStart(6, '0')}`
}

/** The CLI version from the claude_cli check's detail ("Installed (2.4.1 (Claude Code))" → "2.4.1"). */
export function cliVersion(c: ClaudeStatus): string | null {
  const detail = c.checks.find((x) => x.name === 'claude_cli')?.detail ?? ''
  return /\d+\.\d+(?:\.\d+)?/.exec(detail)?.[0] ?? null
}

/** The variable names the key check reports as set (names only; the API never sends a value). */
export function keyVars(c: ClaudeStatus): string[] {
  const detail = c.checks.find((x) => x.name === 'claude_api_key')?.detail ?? ''
  const found = [...new Set(detail.match(/ANTHROPIC_[A-Z_]+/g) ?? [])]
  return found.length ? found : ['ANTHROPIC_API_KEY']
}

/** Which Claude panel to draw, in the order the problems block agents. */
export type ClaudeView = 'ok' | 'missing' | 'logged_out' | 'api_key'

export function claudeView(c: ClaudeStatus): ClaudeView {
  if (!c.installed) return 'missing'
  if (!c.logged_in) return 'logged_out'
  if (!c.api_key_env_clear) return 'api_key'
  return 'ok'
}

const baghdadDay = (ms: number) => new Date(ms + 3 * 3_600_000).toISOString().slice(0, 10)

/** Whether `ms` is today, yesterday or earlier on the Baghdad calendar. */
export function dayOf(ms: number, now: number): 'today' | 'yesterday' | 'earlier' {
  const d = baghdadDay(ms)
  if (d === baghdadDay(now)) return 'today'
  if (d === baghdadDay(now - 86_400_000)) return 'yesterday'
  return 'earlier'
}
