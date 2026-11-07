import { FIGURE_LOCALE, type Lang } from '../../lib/format'
import { ownerDay, TEAM_TZ, teamDay, zoneOffsetMin } from '../../lib/ownerZone'
import type { BackupDoc, BackupRun } from './api'

/*
 * Settings → Backup's arithmetic (design v21 js/pages/settings-backup.js): the status word, the
 * shelf of the last 14 Baghdad days, sizes and durations, the next run and the draft's checks.
 */

const DAY = 86_400_000
export const SHELF_DAYS = 14
export const KEEP_MIN = 1
export const KEEP_MAX = 90
export const LIST_SHORT = 6

/** A run the page asked for that has not been recorded yet (a run is recorded when it finishes). */
export interface PendingRun {
  requestedAt: number
  /** Run ids the page already knew when it pressed Back up now. */
  known: ReadonlySet<string>
}

export type BackupStatus = 'running' | 'none' | 'failed' | 'overdue' | 'ok'

export const ms = (iso: string) => Date.parse(iso)

export const okRuns = (runs: readonly BackupRun[]) => runs.filter((r) => r.status === 'ok')

/** running | none | failed (the newest run failed) | overdue (the API's 26 h `stale`) | ok. */
export function backupStatus(doc: BackupDoc, pending: PendingRun | null): BackupStatus {
  if (pending) return 'running'
  if (!doc.last_good) return 'none'
  if (doc.runs[0]?.status === 'failed') return 'failed'
  return doc.stale ? 'overdue' : 'ok'
}

/** The first run recorded after the page pressed Back up now, if it has arrived. */
export function arrivedRun(doc: BackupDoc | undefined, pending: PendingRun | null): BackupRun | null {
  if (!doc || !pending) return null
  return doc.runs.find((r) => !pending.known.has(r.id)) ?? null
}

/** "684 MB", "1.2 GB", "170 KB": decimal units like Finder. */
export function bytesText(b: number, lang: Lang): string {
  const n = (v: number, d: number) => new Intl.NumberFormat(FIGURE_LOCALE[lang], { maximumFractionDigits: d, minimumFractionDigits: d }).format(v)
  if (b >= 1e9) return `${n(b / 1e9, 1)} GB`
  if (b >= 1e6) return `${n(Math.round(b / 1e6), 0)} MB`
  return `${n(Math.max(1, Math.round(b / 1e3)), 0)} KB`
}

/** "0:48", "12:05": minutes and seconds. */
export function mmss(milliseconds: number): string {
  const s = Math.max(0, Math.round(milliseconds / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** "03:00" → 180. */
export const hm = (time: string) => {
  const [h = 0, m = 0] = time.split(':').map(Number)
  return h * 60 + m
}

/** 1620 → "03:00" (wraps around the day). */
export function clock(minutes: number): string {
  const v = ((minutes % 1440) + 1440) % 1440
  return `${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`
}

/** The instant `time` (Baghdad) falls on, on the Baghdad day `iso`. */
export function teamInstant(iso: string, time: string, ref: number): number {
  return Date.parse(`${iso}T00:00:00Z`) + (hm(time) - zoneOffsetMin(TEAM_TZ, ref)) * 60_000
}

/** The next scheduled run: backup.time in Baghdad, the first one after `now`. */
export function nextRun(time: string, now: number): number {
  let at = teamInstant(teamDay(now), time, now)
  if (at <= now) at += DAY
  return at
}

/** Calendar days from the owner's today to the owner's day of `at` (−1 yesterday, 1 tomorrow). */
export function dayDiff(at: number, now: number): number {
  return Math.round((Date.parse(`${ownerDay(at)}T00:00:00Z`) - Date.parse(`${ownerDay(now)}T00:00:00Z`)) / DAY)
}

export type SlotKind = 'ok' | 'failed' | 'missed' | 'unknown'

export interface Slot {
  /** Baghdad day, Y-m-d. */
  iso: string
  kind: SlotKind
  run: BackupRun | null
  /** The bar's height, 0..1 of the largest good backup shown. */
  v: number
  latest: boolean
  today: boolean
}

/**
 * The last 14 Baghdad days, today last. GET /api/backup lists only the newest 10 runs, so when the
 * list is full, days before its oldest run are `unknown`, never shown as missed.
 */
export function shelfSlots(doc: BackupDoc, now: number, listed = 10): Slot[] {
  const today = teamDay(now)
  const t0 = Date.parse(`${today}T00:00:00Z`)
  const ok = okRuns(doc.runs)
  const max = Math.max(1, ...ok.map((r) => r.bytes ?? 0))
  const oldest = doc.runs[doc.runs.length - 1]
  const cutoff = doc.runs.length >= listed && oldest ? teamDay(ms(oldest.started_at)) : null
  return Array.from({ length: SHELF_DAYS }, (_, i) => {
    const iso = new Date(t0 - (SHELF_DAYS - 1 - i) * DAY).toISOString().slice(0, 10)
    const runs = doc.runs.filter((r) => teamDay(ms(r.started_at)) === iso)
    const good = runs.find((r) => r.status === 'ok') ?? null
    const bad = runs.find((r) => r.status === 'failed') ?? null
    const kind: SlotKind = good ? 'ok' : bad ? 'failed' : cutoff && iso < cutoff ? 'unknown' : 'missed'
    return {
      iso,
      kind,
      run: good ?? bad,
      v: good ? (good.bytes ?? 0) / max : 0,
      latest: !!good && good.id === ok[0]?.id,
      today: i === SHELF_DAYS - 1,
    }
  })
}

/** The bytes of the copies still on disk: the newest `keep` good runs listed. */
export function keptBytes(doc: BackupDoc): { bytes: number; copies: number } {
  const kept = okRuns(doc.runs).slice(0, doc.settings.keep)
  return { bytes: kept.reduce((s, r) => s + (r.bytes ?? 0), 0), copies: kept.length }
}

/* ---- the draft ---------------------------------------------------------------- */

export interface Draft {
  enabled: boolean
  folder: string
  time: string
  /** The keep field's text, so a half-typed number survives. */
  keep: string
}

export const draftFrom = (doc: BackupDoc): Draft => ({
  enabled: doc.settings.enabled,
  folder: doc.settings.folder,
  time: doc.settings.time,
  keep: String(doc.settings.keep),
})

const BIDI = /[‎‏⁦-⁩]/g
export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

/** "3:00", "3.00", "300" → "03:00"; anything else unchanged. */
export function normTime(v: string): string {
  const s = v.replace(BIDI, '').trim()
  let m = /^(\d{1,2})[:.](\d{2})$/.exec(s)
  if (m?.[1] && m[2]) return `${m[1].padStart(2, '0')}:${m[2]}`
  m = /^(\d{1,2})(\d{2})$/.exec(s)
  if (m?.[1] && m[2]) return `${m[1].padStart(2, '0')}:${m[2]}`
  return s
}

export function keepValue(text: string): number | null {
  const s = text.replace(BIDI, '').trim()
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return n >= KEEP_MIN && n <= KEEP_MAX ? n : null
}

export type DraftField = 'folder' | 'time' | 'keep'

/**
 * What the page can tell before asking: an empty folder, a bad time, keep out of range. Whether a
 * folder is absolute, outside the repository and writable is the server's call (its 422 shows).
 */
export function clientErrors(d: Draft): Partial<Record<DraftField, 'folderEmpty' | 'time' | 'keep'>> {
  const out: Partial<Record<DraftField, 'folderEmpty' | 'time' | 'keep'>> = {}
  if (d.folder.trim() === '') out.folder = 'folderEmpty'
  if (!TIME_RE.test(d.time)) out.time = 'time'
  if (keepValue(d.keep) === null) out.keep = 'keep'
  return out
}

/** The changed backup.* keys, ready for PATCH /api/settings. */
export function changesFor(d: Draft, doc: BackupDoc): Record<string, string | number | boolean> {
  const s = doc.settings
  const out: Record<string, string | number | boolean> = {}
  if (d.enabled !== s.enabled) out['backup.enabled'] = d.enabled
  if (d.folder.trim() !== s.folder) out['backup.folder'] = d.folder.trim()
  if (d.time !== s.time) out['backup.time'] = d.time
  const keep = keepValue(d.keep)
  if (keep !== null && keep !== s.keep) out['backup.keep'] = keep
  return out
}

/** The restore commands for one dump (docs/RUNBOOK.md, Backup / restore). */
export function restoreCommands(path: string): [string, string, string] {
  const psql = 'PGPASSWORD=<DB_PASSWORD> psql -h 127.0.0.1 -p 5433 -U atlas -d atlas -c'
  return [
    './atlas down',
    [
      `${psql} "SELECT timescaledb_pre_restore();"`,
      `PGPASSWORD=<DB_PASSWORD> /opt/homebrew/opt/libpq/bin/pg_restore --clean --if-exists -h 127.0.0.1 -p 5433 -U atlas -d atlas "${path}"`,
      `${psql} "SELECT timescaledb_post_restore();"`,
    ].join('\n'),
    './atlas up && ./atlas doctor',
  ]
}
