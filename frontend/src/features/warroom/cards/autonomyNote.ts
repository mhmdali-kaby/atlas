/*
 * The AutonomyNote's data (decision 254, ATL-212/213, design v18 §4.6, ATL-219). Built only from
 * the risk check's own stored autonomy values (checks[8].values for an entry, the `autonomy` check
 * at n = 7 for a position action) and the proposal's kind — never from the proposal's requested
 * size, the settings page or a fixture. Mirrors services/trading_core/src/trading_core/risk/rules.py
 * `decide_autonomy` and the prototype's js/autonomy-note.js `of()`.
 */

export type AutonomyNote = 'above_ask_threshold' | 'risk_reducing' | 'halted' | 'notional_unknown' | 'level_not_enabled'

export interface AutonomyNoteValues {
  autonomyLevel: number
  maxEnabledLevel: number
  /** The final notional (entries), the close notional (close/partial_close), or the whole
   *  position's notional (amend_stop/amend_targets); null when it could not be worked out. */
  notionalUsd: string | null
  askAboveUsd: string
  note?: AutonomyNote
}

export type ProposalActionKind = 'entry' | 'amend_stop' | 'amend_targets' | 'close' | 'partial_close'

export type AutonomyDecision = 'send_now' | 'awaiting_owner' | 'advise_only'

export type AutonomyIcon = 'send' | 'hand' | 'eye' | 'circle-pause' | 'circle-dashed' | 'lock'

/** The reason key (an `autonomy-note.json` top-level key); also used as `data-au-note`. */
export type AutonomyKey = 'l1' | 'l2' | 'notEnabled' | 'halted' | 'l4' | 'closing' | 'unknown' | 'above' | 'abovePos' | 'within' | 'withinPos'

export interface AutonomyResolved {
  decision: AutonomyDecision
  key: AutonomyKey
  level: number
  /** A dollar comparison decided it: show the mini ask line. */
  gauge: boolean
  icon: AutonomyIcon
  halted: boolean
}

const POSITION_ACTION_KINDS = new Set<string>(['amend_stop', 'amend_targets', 'close', 'partial_close'])

/** amend_stop and amend_targets compare the whole position's notional; close and partial_close
 *  never wait at level 3 (risk-reducing), so they never carry the "whole position" wording. */
export const isPositionActionKind = (k: string): k is ProposalActionKind => POSITION_ACTION_KINDS.has(k)

const has = (v: string | null | undefined): v is string => v !== null && v !== undefined && v !== ''

/** The decision and the reason key for one autonomy check (first match wins, as `decide_autonomy`). */
export function resolveAutonomy(v: AutonomyNoteValues, kind: ProposalActionKind = 'entry'): AutonomyResolved {
  const level = v.autonomyLevel
  const note = v.note ?? null
  const wholePosition = isPositionActionKind(kind) && kind !== 'close' && kind !== 'partial_close'
  let decision: AutonomyDecision
  let key: AutonomyKey
  let gauge = false
  if (level <= 1) {
    decision = 'advise_only'
    key = 'l1'
  } else if (level === 2) {
    decision = 'awaiting_owner'
    key = 'l2'
  } else if (note === 'level_not_enabled') {
    decision = 'awaiting_owner'
    key = 'notEnabled'
  } else if (note === 'halted') {
    decision = 'awaiting_owner'
    key = 'halted'
  } else if (level >= 4) {
    decision = 'send_now'
    key = 'l4'
  } else if (note === 'risk_reducing') {
    decision = 'send_now'
    key = 'closing'
  } else if (note === 'notional_unknown' || !has(v.notionalUsd)) {
    decision = 'awaiting_owner'
    key = 'unknown'
  } else if (note === 'above_ask_threshold') {
    decision = 'awaiting_owner'
    key = wholePosition ? 'abovePos' : 'above'
    gauge = true
  } else {
    decision = 'send_now'
    key = wholePosition ? 'withinPos' : 'within'
    gauge = true
  }
  const icon: AutonomyIcon = key === 'halted' ? 'circle-pause' : key === 'unknown' ? 'circle-dashed' : key === 'notEnabled' ? 'lock' : decision === 'send_now' ? 'send' : decision === 'advise_only' ? 'eye' : 'hand'
  return { decision, key, level, gauge, icon, halted: key === 'halted' }
}

/** The mini ask line's geometry: the larger of the two amounts sits at 80% of the width. */
export interface AutonomyGauge {
  /** The ask-above amount's position, 0..1. */
  cut: number
  /** This trade's position, 0..1. */
  at: number
  above: boolean
}

export function gaugeOf(v: AutonomyNoteValues): AutonomyGauge | null {
  const n = Number(v.notionalUsd ?? '')
  const a = Number(v.askAboveUsd)
  if (!Number.isFinite(n) || !Number.isFinite(a)) return null
  const max = Math.max(n, a, 1) / 0.8
  return { cut: Math.min(1, a / max), at: Math.min(1, n / max), above: n > a }
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

function decStr(v: unknown): string | null {
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  if (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v.trim())) return v.trim()
  return null
}

function intOf(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return Math.trunc(v)
  if (typeof v === 'string' && /^-?\d+$/.test(v.trim())) return Number(v)
  return null
}

const NOTES = new Set<AutonomyNote>(['above_ask_threshold', 'risk_reducing', 'halted', 'notional_unknown', 'level_not_enabled'])

/** The autonomy check's own `values` (raw JSON, as trading_core stores it) → `AutonomyNoteValues`,
 *  or null when it is not a usable autonomy check. Never invents a level or an ask-above amount. */
export function readAutonomyValues(raw: unknown): AutonomyNoteValues | null {
  if (!isObj(raw)) return null
  const level = intOf(raw.autonomy_level)
  const maxLevel = intOf(raw.max_enabled_level)
  const ask = decStr(raw.ask_above_usd)
  if (level === null || maxLevel === null || ask === null) return null
  const note = typeof raw.note === 'string' && NOTES.has(raw.note as AutonomyNote) ? (raw.note as AutonomyNote) : undefined
  return { autonomyLevel: level, maxEnabledLevel: maxLevel, notionalUsd: decStr(raw.notional_usd), askAboveUsd: ask, note }
}
