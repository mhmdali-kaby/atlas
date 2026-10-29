import type { ApiIssue, ApiJob, AssetClass, BackfillReport, IssueKind } from './api'

/*
 * Everything Settings → Data derives on the client (design §7 "Derived on the client"), kept pure
 * so it is unit-tested apart from the page.
 */

export type DatasetKey =
  | 'candles:1D'
  | 'candles:4H'
  | 'candles:1H'
  | 'candles:15m'
  | 'candles:5m'
  | 'candles:1m'
  | 'funding'
  | 'open_interest'

export interface DatasetDef {
  key: DatasetKey
  /** The table header code (an LTR island) for candles. */
  code: string | null
  /** Bar length in ms (candles only). */
  barMs: number | null
  /** No configured depth and no percent until done ("as far back as OKX goes"). */
  open: boolean
}

const MIN = 60_000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

/** Table and sheet order (design §5.6). */
export const DATASETS: readonly DatasetDef[] = [
  { key: 'candles:1D', code: '1D', barMs: DAY, open: false },
  { key: 'candles:4H', code: '4H', barMs: 4 * HOUR, open: false },
  { key: 'candles:1H', code: '1H', barMs: HOUR, open: false },
  { key: 'candles:15m', code: '15m', barMs: 15 * MIN, open: false },
  { key: 'candles:5m', code: '5m', barMs: 5 * MIN, open: false },
  { key: 'candles:1m', code: '1m', barMs: MIN, open: false },
  { key: 'funding', code: null, barMs: null, open: true },
  { key: 'open_interest', code: null, barMs: null, open: true },
]
export const DATASET: Readonly<Record<DatasetKey, DatasetDef>> = Object.fromEntries(DATASETS.map((d) => [d.key, d])) as Record<
  DatasetKey,
  DatasetDef
>
export const CANDLE_KEYS = DATASETS.filter((d) => !d.open).map((d) => d.key)
const isDatasetKey = (k: string): k is DatasetKey => k in DATASET

/** The order trading_core claims jobs in (ATL-46 `dataset_order`): Fetching now lists running jobs this way. */
export const RUNNER_ORDER: readonly DatasetKey[] = [
  'candles:1D',
  'candles:1H',
  'candles:4H',
  'funding',
  'open_interest',
  'candles:15m',
  'candles:5m',
  'candles:1m',
]

/**
 * config/defaults.yaml backfill.depth_days, used only before any job exists (the not-started plan).
 * Once jobs exist the page reads each dataset's depth_days from the Data API.
 */
export const FALLBACK_DEPTH_DAYS: Readonly<Record<DatasetKey, number | null>> = {
  'candles:1D': null,
  'candles:4H': 730,
  'candles:1H': 730,
  'candles:15m': 180,
  'candles:5m': 180,
  'candles:1m': 30,
  funding: null,
  open_interest: null,
}

export const CLASS_ORDER: readonly AssetClass[] = ['crypto', 'stock', 'etf', 'index', 'commodity', 'other']

/** What one job or instrument is doing, in the design's words. */
export type Status = 'done' | 'fetching' | 'waiting' | 'paused' | 'failed'
export const STATUS_ORDER: readonly Status[] = ['done', 'fetching', 'waiting', 'paused', 'failed']

export function jobStatus(job: ApiJob): Status {
  if (job.status === 'running') return 'fetching'
  if (job.status === 'pending') return 'waiting'
  return job.status
}

/** 0..1 of a job's history, or null when it has no percent (open-ended and not done). */
export function jobFraction(job: ApiJob): number | null {
  if (job.status === 'done') return 1
  if (job.expected === null || job.expected <= 0) return null
  return Math.min(job.rows_written, job.expected) / job.expected
}

export interface Inst {
  instId: string
  assetClass: AssetClass
  /** The instrument's own jobs plus its currency's open-interest job (ATL-46: OI is per ccy). */
  jobs: Partial<Record<DatasetKey, ApiJob>>
  /** The inst_id the open-interest job belongs to ("BTC"), when there is one. */
  oiOwner: string | null
  status: Status
  /** 0..1: Σ min(rows, expected) ÷ Σ expected over its candle jobs. */
  pct: number
}

export function instStatus(jobs: readonly ApiJob[]): Status {
  const s = jobs.map(jobStatus)
  if (s.includes('failed')) return 'failed'
  if (s.includes('fetching')) return 'fetching'
  if (s.length > 0 && s.every((x) => x === 'done')) return 'done'
  if (s.includes('paused')) return 'paused'
  return 'waiting'
}

function candleSums(jobs: readonly (ApiJob | undefined)[]) {
  let written = 0
  let expected = 0
  for (const j of jobs) {
    if (!j || j.expected === null) continue
    // A done job is complete even when OKX had fewer bars than expected (the API says 100% too).
    written += j.status === 'done' ? j.expected : Math.min(j.rows_written, j.expected)
    expected += j.expected
  }
  return { written, expected }
}

/** A currency-level rubik row (open interest): its inst_id is a bare ccy such as "BTC". */
const isCcyRow = (instId: string) => !instId.includes('-')

export interface FailedJob {
  instId: string
  dataset: DatasetKey
}

export interface DatasetTotal {
  written: number
  expected: number
  pct: number
  jobs: number
  done: number
  running: number
  paused: number
  failed: number
  all: boolean
}

export interface Model {
  insts: Inst[]
  byId: Map<string, Inst>
  jobs: number
  /** Candle bars across every instrument. */
  written: number
  expected: number
  pct: number
  counts: Record<Status, number>
  failedJobs: FailedJob[]
  anyRunning: boolean
  anyPending: boolean
  anyPaused: boolean
  /** Newest job updated_at, ms. */
  lastMoved: number | null
  datasets: Record<DatasetKey, DatasetTotal>
  issues: ApiIssue[]
}

export function buildModel(report: BackfillReport): Model {
  const ccy = new Map<string, ApiJob>()
  const rows = report.instruments.filter((i) => {
    if (!isCcyRow(i.inst_id)) return true
    const oi = i.datasets.find((d) => d.dataset === 'open_interest')
    if (oi) ccy.set(i.inst_id, oi)
    return false
  })

  const insts: Inst[] = rows.map((row) => {
    const jobs: Partial<Record<DatasetKey, ApiJob>> = {}
    for (const j of row.datasets) if (isDatasetKey(j.dataset)) jobs[j.dataset] = j
    const base = row.inst_id.split('-')[0] ?? row.inst_id
    let oiOwner: string | null = null
    if (!jobs.open_interest && ccy.has(base)) {
      jobs.open_interest = ccy.get(base)
      oiOwner = base
    }
    const list = Object.values(jobs).filter((j): j is ApiJob => j !== undefined)
    const { written, expected } = candleSums(CANDLE_KEYS.map((k) => jobs[k]))
    return {
      instId: row.inst_id,
      assetClass: row.asset_class ?? 'other',
      jobs,
      oiOwner,
      status: instStatus(list),
      pct: expected > 0 ? written / expected : list.length > 0 && list.every((j) => j.status === 'done') ? 1 : 0,
    }
  })
  const rank = (c: AssetClass) => CLASS_ORDER.indexOf(c)
  insts.sort((a, b) => rank(a.assetClass) - rank(b.assetClass) || (a.instId < b.instId ? -1 : a.instId > b.instId ? 1 : 0))

  // Every job exactly once, for the summary and What Atlas keeps (a ccy's OI job is shared by its pairs).
  const allJobs: { instId: string; job: ApiJob }[] = report.instruments.flatMap((i) =>
    i.datasets.map((job) => ({ instId: i.inst_id, job })),
  )
  const candles = candleSums(allJobs.filter((x) => x.job.dataset.startsWith('candles:')).map((x) => x.job))

  const counts: Record<Status, number> = { done: 0, fetching: 0, waiting: 0, paused: 0, failed: 0 }
  for (const i of insts) counts[i.status]++

  const datasets = Object.fromEntries(
    DATASETS.map((d) => {
      const list = allJobs.filter((x) => x.job.dataset === d.key).map((x) => x.job)
      let written = 0
      let expected = 0
      for (const j of list) {
        written += j.expected === null ? j.rows_written : j.status === 'done' ? j.expected : Math.min(j.rows_written, j.expected)
        if (j.expected !== null) expected += j.expected
      }
      const by = (s: ApiJob['status']) => list.filter((j) => j.status === s).length
      const done = by('done')
      const total: DatasetTotal = {
        written,
        expected,
        pct: d.open ? (list.length > 0 && done === list.length ? 1 : 0) : expected > 0 ? written / expected : 0,
        jobs: list.length,
        done,
        running: by('running'),
        paused: by('paused'),
        failed: by('failed'),
        all: list.length > 0 && done === list.length,
      }
      return [d.key, total]
    }),
  ) as Record<DatasetKey, DatasetTotal>

  const failedJobs: FailedJob[] = []
  for (const key of RUNNER_ORDER)
    for (const { instId, job } of allJobs) if (job.dataset === key && job.status === 'failed') failedJobs.push({ instId, dataset: key })

  const moved = allJobs.map((x) => Date.parse(x.job.updated_at)).filter((n) => !Number.isNaN(n))

  return {
    insts,
    byId: new Map(insts.map((i) => [i.instId, i])),
    jobs: allJobs.length,
    written: candles.written,
    expected: candles.expected,
    pct: candles.expected > 0 ? candles.written / candles.expected : 0,
    counts,
    failedJobs,
    anyRunning: allJobs.some((x) => x.job.status === 'running'),
    anyPending: allJobs.some((x) => x.job.status === 'pending'),
    anyPaused: allJobs.some((x) => x.job.status === 'paused'),
    lastMoved: moved.length > 0 ? Math.max(...moved) : null,
    datasets,
    issues: report.quality_issues,
  }
}

/** Running jobs in the order the runner claims them, for Fetching now. */
export function runningJobs(m: Model): { instId: string; dataset: DatasetKey; job: ApiJob }[] {
  const out: { instId: string; dataset: DatasetKey; job: ApiJob }[] = []
  const seen = new Set<ApiJob>()
  for (const key of RUNNER_ORDER)
    for (const i of m.insts) {
      const job = i.jobs[key]
      if (job && job.status === 'running' && !seen.has(job)) {
        seen.add(job)
        out.push({ instId: key === 'open_interest' && i.oiOwner ? i.oiOwner : i.instId, dataset: key, job })
      }
    }
  return out
}

/** TL (ATL-49): stalled = no job's updated_at has moved for 4 min while any job is running. */
export const STALL_MS = 4 * MIN

export type SummaryState = 'notStarted' | 'running' | 'done' | 'paused' | 'stalled'

export function summaryState(m: Model, nowMs: number): SummaryState {
  if (m.jobs === 0) return 'notStarted'
  if (m.anyRunning && m.lastMoved !== null && nowMs - m.lastMoved >= STALL_MS) return 'stalled'
  if (m.anyRunning || m.anyPending) return 'running'
  if (m.anyPaused) return 'paused'
  if (m.counts.done === m.insts.length && m.failedJobs.length === 0) return 'done'
  return 'running'
}

/** Whole minutes since the last movement, for the stalled sub-line. */
export const stalledMinutes = (m: Model, nowMs: number) => (m.lastMoved === null ? 0 : Math.floor((nowMs - m.lastMoved) / MIN))

export type InstFilter = 'all' | 'progress' | 'failed' | 'done'

export function filterInsts(insts: readonly Inst[], filter: InstFilter, query: string): Inst[] {
  const q = query.trim().toUpperCase()
  return insts.filter((i) => {
    if (filter === 'done' && i.status !== 'done') return false
    if (filter === 'failed' && i.status !== 'failed') return false
    if (filter === 'progress' && (i.status === 'done' || i.status === 'failed')) return false
    return !q || i.instId.toUpperCase().includes(q)
  })
}

const KIND_RANK: Record<IssueKind, number> = { gap: 0, duplicate: 1, outlier: 2, zero_volume: 3 }

/** Open: gap → duplicate → outlier → zero volume, newest first. Repaired: newest repair first. */
export function sortIssues(issues: readonly ApiIssue[], tab: 'open' | 'repaired'): ApiIssue[] {
  const list = issues.filter((q) => (tab === 'open' ? q.repaired_at === null : q.repaired_at !== null))
  return tab === 'open'
    ? list.sort((a, b) => KIND_RANK[a.kind] - KIND_RANK[b.kind] || Date.parse(b.detected_at) - Date.parse(a.detected_at))
    : list.sort((a, b) => Date.parse(b.repaired_at!) - Date.parse(a.repaired_at!))
}

/** Bars in an issue's window: (to − from) ÷ bar + 1. */
export function issueBars(q: ApiIssue): number {
  const bar = isDatasetKey(q.dataset) ? DATASET[q.dataset].barMs : null
  if (!bar) return 1
  return Math.max(1, Math.round((Date.parse(q.to_ts) - Date.parse(q.from_ts)) / bar) + 1)
}

/* ---- figures (always LTR islands in the UI) ---- */

/** Floored, so 99.6% never reads 100%. */
export const pctText = (p: number) => `${Math.floor(p * 100 + 1e-9)}%`

const plain = new Intl.NumberFormat('en-US')
const compactSmall = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 0 })
const compactBig = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
export const exact = (n: number) => plain.format(n)
/** Compact from 10,000 ("24.7M"), exact below. */
export const compact = (n: number) => (n < 10_000 ? plain.format(n) : (n < 1e6 ? compactSmall : compactBig).format(n))

/** Decimal units, as macOS shows sizes: 3.27 GB, 52.3 MB, 24.6 KB. */
export function bytesText(b: number): string {
  for (const [unit, v] of [
    ['GB', 1e9],
    ['MB', 1e6],
    ['KB', 1e3],
  ] as const) {
    if (b >= v) {
      const x = b / v
      return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${unit}`
    }
  }
  return `${b} B`
}

/** Bars written per second over the last two minutes of polls; null until 30 s of samples. */
export interface Sample {
  at: number
  written: number
}
export const RATE_WINDOW_MS = 2 * MIN
export const RATE_MIN_MS = 30_000

export function etaSeconds(samples: readonly Sample[], remaining: number): number | null {
  if (samples.length < 2 || remaining <= 0) return null
  const last = samples[samples.length - 1]!
  const first = samples.find((s) => last.at - s.at <= RATE_WINDOW_MS) ?? samples[0]!
  const span = last.at - first.at
  if (span < RATE_MIN_MS) return null
  const rate = (last.written - first.written) / (span / 1000)
  return rate > 0 ? remaining / rate : null
}

/** Rounds the time left as the design says: < 1 min; exact to 10 min; then to 5; from 1 h, h + min to 10. */
export function etaParts(sec: number): { h: number; m: number } | 'lessThanMinute' {
  if (sec < 60) return 'lessThanMinute'
  let h = Math.floor(sec / 3600)
  let m = Math.round((sec % 3600) / 60)
  if (h >= 1) m = Math.round(m / 10) * 10
  else if (m > 10) m = Math.round(m / 5) * 5
  if (m === 60) {
    h += 1
    m = 0
  }
  return { h, m }
}

/** A dataset as a translation key: i18next reads ":" as its namespace separator, so "candles:1H" → "candles_1H". */
export const tKey = (dataset: string) => dataset.replace(':', '_')
