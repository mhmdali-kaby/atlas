import { describe, expect, it } from 'vitest'
import type { ApiJob, BackfillReport } from './api'
import {
  buildModel,
  bytesText,
  compact,
  etaParts,
  etaSeconds,
  filterInsts,
  issueBars,
  pctText,
  runningJobs,
  sortIssues,
  STALL_MS,
  summaryState,
  tKey,
} from './model'

const NOW = Date.parse('2026-10-29T12:00:00Z')

function job(dataset: string, over: Partial<ApiJob> = {}): ApiJob {
  return {
    dataset,
    status: 'done',
    depth_days: 730,
    target_from: '2024-10-29T12:00:00Z',
    cursor_ts: '2024-10-29T12:00:00Z',
    rows_written: 100,
    expected: 100,
    percent: '100.0',
    last_error: null,
    updated_at: new Date(NOW).toISOString(),
    ...over,
  }
}

function report(instruments: BackfillReport['instruments'], issues: BackfillReport['quality_issues'] = []): BackfillReport {
  return {
    env: 'demo',
    as_of: new Date(NOW).toISOString(),
    instruments,
    totals: { instruments: instruments.length, jobs: 0, by_status: { pending: 0, running: 0, done: 0, failed: 0, paused: 0 }, rows_written: 0, percent: null },
    quality: { open: 0, open_by_kind: { gap: 0, duplicate: 0, zero_volume: 0, outlier: 0 } },
    quality_issues: issues,
  }
}

describe('buildModel', () => {
  const r = report([
    { inst_id: 'XAU-USDT-SWAP', asset_class: 'commodity', open_issues: 0, datasets: [job('candles:1H'), job('candles:1m', { status: 'running', rows_written: 30, expected: 100 })] },
    { inst_id: 'BTC-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H'), job('funding', { expected: null, percent: null, status: 'running', rows_written: 40 })] },
    { inst_id: 'BTC', asset_class: null, open_issues: 0, datasets: [job('open_interest', { status: 'failed', expected: null, percent: null, rows_written: 5 })] },
    { inst_id: 'TSLA-USDT-SWAP', asset_class: 'stock', open_issues: 0, datasets: [job('candles:1H', { status: 'pending', rows_written: 0 })] },
  ])
  const m = buildModel(r)

  it('orders by asset class then inst_id, and folds the ccy OI job into its pair', () => {
    expect(m.insts.map((i) => i.instId)).toEqual(['BTC-USDT-SWAP', 'TSLA-USDT-SWAP', 'XAU-USDT-SWAP'])
    const btc = m.byId.get('BTC-USDT-SWAP')!
    expect(btc.jobs.open_interest?.status).toBe('failed')
    expect(btc.oiOwner).toBe('BTC')
    expect(btc.status).toBe('failed')
  })

  it('derives each instrument status and candle percent', () => {
    expect(m.byId.get('XAU-USDT-SWAP')!.status).toBe('fetching')
    expect(m.byId.get('XAU-USDT-SWAP')!.pct).toBeCloseTo(130 / 200)
    expect(m.byId.get('TSLA-USDT-SWAP')!.status).toBe('waiting')
    expect(m.counts).toEqual({ done: 0, fetching: 1, waiting: 1, paused: 0, failed: 1 })
  })

  it('counts every job once, sums candle bars, and lists failed jobs', () => {
    expect(m.jobs).toBe(6)
    expect(m.written).toBe(100 + 30 + 100 + 0)
    expect(m.expected).toBe(400)
    expect(m.failedJobs).toEqual([{ instId: 'BTC', dataset: 'open_interest' }])
    expect(m.datasets.funding.running).toBe(1)
    expect(m.datasets['candles:1H'].jobs).toBe(3)
  })

  it('lists running jobs in runner order (1H, funding, … 1m)', () => {
    expect(runningJobs(m).map((x) => `${x.instId}|${x.dataset}`)).toEqual(['BTC-USDT-SWAP|funding', 'XAU-USDT-SWAP|candles:1m'])
  })

  it('treats a done job as complete even when OKX had fewer bars than expected', () => {
    const d = buildModel(report([{ inst_id: 'A-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1m', { rows_written: 90, expected: 100 })] }]))
    expect(d.byId.get('A-USDT-SWAP')!.pct).toBe(1)
    expect(d.pct).toBe(1)
  })
})

describe('summaryState', () => {
  const one = (status: ApiJob['status'], updated = NOW) =>
    buildModel(report([{ inst_id: 'A-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H', { status, updated_at: new Date(updated).toISOString() })] }]))

  it('is notStarted with no jobs, and done, paused or running from the jobs', () => {
    expect(summaryState(buildModel(report([])), NOW)).toBe('notStarted')
    expect(summaryState(one('done'), NOW)).toBe('done')
    expect(summaryState(one('paused'), NOW)).toBe('paused')
    expect(summaryState(one('running'), NOW)).toBe('running')
    expect(summaryState(one('pending'), NOW)).toBe('running')
  })

  it('is stalled when a job runs but nothing has moved for 4 minutes', () => {
    expect(summaryState(one('running', NOW - STALL_MS + 1000), NOW)).toBe('running')
    expect(summaryState(one('running', NOW - STALL_MS), NOW)).toBe('stalled')
  })
})

describe('filters and issues', () => {
  const m = buildModel(
    report([
      { inst_id: 'BTC-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H')] },
      { inst_id: 'ETH-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H', { status: 'failed' })] },
      { inst_id: 'SOL-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H', { status: 'paused' })] },
    ]),
  )
  it('filters by view and case-insensitive substring', () => {
    expect(filterInsts(m.insts, 'done', '').map((i) => i.instId)).toEqual(['BTC-USDT-SWAP'])
    expect(filterInsts(m.insts, 'failed', '').map((i) => i.instId)).toEqual(['ETH-USDT-SWAP'])
    expect(filterInsts(m.insts, 'progress', '').map((i) => i.instId)).toEqual(['SOL-USDT-SWAP'])
    expect(filterInsts(m.insts, 'all', 'eth').map((i) => i.instId)).toEqual(['ETH-USDT-SWAP'])
  })

  it('sorts open issues by kind then newest, repaired by newest repair', () => {
    const base = { inst_id: 'X', dataset: 'candles:1m', from_ts: '2026-10-29T10:00:00Z', to_ts: '2026-10-29T10:03:00Z', detail: null }
    const issues = [
      { ...base, id: 'z', kind: 'zero_volume' as const, detected_at: '2026-10-29T11:00:00Z', repaired_at: null },
      { ...base, id: 'g1', kind: 'gap' as const, detected_at: '2026-10-29T09:00:00Z', repaired_at: null },
      { ...base, id: 'g2', kind: 'gap' as const, detected_at: '2026-10-29T10:00:00Z', repaired_at: null },
      { ...base, id: 'r1', kind: 'gap' as const, detected_at: '2026-10-29T10:00:00Z', repaired_at: '2026-10-29T10:05:00Z' },
      { ...base, id: 'r2', kind: 'gap' as const, detected_at: '2026-10-29T10:00:00Z', repaired_at: '2026-10-29T11:05:00Z' },
    ]
    expect(sortIssues(issues, 'open').map((q) => q.id)).toEqual(['g2', 'g1', 'z'])
    expect(sortIssues(issues, 'repaired').map((q) => q.id)).toEqual(['r2', 'r1'])
    expect(issueBars(issues[0]!)).toBe(4)
  })
})

describe('figures', () => {
  it('floors percents, so 99.6% never reads 100%', () => {
    expect(pctText(0.996)).toBe('99%')
    expect(pctText(1)).toBe('100%')
    expect(pctText(0.62)).toBe('62%')
  })
  it('compacts from 10,000 and formats sizes in decimal units', () => {
    expect(compact(9999)).toBe('9,999')
    expect(compact(24_700_000)).toBe('24.7M')
    expect(bytesText(3_270_000_000)).toBe('3.27 GB')
    expect(bytesText(52_300_000)).toBe('52.3 MB')
    expect(bytesText(512)).toBe('512 B')
  })
  it('estimates the time left from 30 s or more of samples and rounds it as designed', () => {
    expect(etaSeconds([{ at: 0, written: 0 }, { at: 20_000, written: 1000 }], 1000)).toBeNull()
    expect(etaSeconds([{ at: 0, written: 0 }, { at: 60_000, written: 6000 }], 9000)).toBe(90)
    expect(etaParts(30)).toBe('lessThanMinute')
    expect(etaParts(7 * 60)).toEqual({ h: 0, m: 7 })
    expect(etaParts(23 * 60)).toEqual({ h: 0, m: 25 })
    expect(etaParts(2 * 3600 + 34 * 60)).toEqual({ h: 2, m: 30 })
  })
  it('turns a dataset into a translation key without i18next’s namespace separator', () => {
    expect(tKey('candles:1H')).toBe('candles_1H')
    expect(tKey('funding')).toBe('funding')
  })
})
