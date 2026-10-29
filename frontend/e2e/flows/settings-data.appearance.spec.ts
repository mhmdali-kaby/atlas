import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import type { Page, Route } from '@playwright/test'
import { BACKEND_DIR, BACKEND_ENV, EVIDENCE_DIR, FRONTEND_DIR, withEnv } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-49: Settings → Data against the real Laravel (ATL-48's Data API) in the four appearance
 * projects (en/ar × light/dark). BackfillDemoSeeder gives the demo env eight instruments with jobs
 * in every state; it is re-run before each test so the running jobs' updated_at stays fresh (the page
 * calls a backfill stalled after 4 minutes without movement). Re-sync answers (202, 429) and the
 * empty and error states are routed in the browser, so no test starts a real backfill in trading_core.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-49')

type Strings = Record<string, unknown>
const locale = (lang: string): Strings =>
  JSON.parse(readFileSync(path.join(FRONTEND_DIR, 'src/i18n/locales', lang, 'settings-data.json'), 'utf8')) as Strings
const str = (s: Strings, key: string): string => key.split('.').reduce<unknown>((o, k) => (o as Strings)[k], s) as string
/** A locale string with its <tags> removed (Trans renders them as elements). */
const plain = (s: string) => s.replace(/<\/?[a-z]+>/g, '')

interface Job {
  dataset: string
  status: string
  rows_written: number
  expected: number | null
}
interface Inst {
  inst_id: string
  asset_class: string | null
  datasets: Job[]
}
interface Report {
  instruments: Inst[]
  quality_issues: unknown[]
  totals: Record<string, unknown>
  [k: string]: unknown
}

const STATUS: Record<string, string> = { running: 'fetching', pending: 'waiting', done: 'done', failed: 'failed', paused: 'paused' }
const pct = (j: Job) => (j.status === 'done' ? 100 : j.expected ? Math.floor((Math.min(j.rows_written, j.expected) / j.expected) * 100 + 1e-9) : 0)
function bytesText(b: number): string {
  for (const [u, v] of [['GB', 1e9], ['MB', 1e6], ['KB', 1e3]] as const) {
    if (b >= v) {
      const x = b / v
      return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${u}`
    }
  }
  return `${b} B`
}

test.use({ viewport: { width: 1280, height: 900 } })

test.beforeEach(() => {
  execFileSync('php', ['artisan', 'db:seed', '--class=Database\\Seeders\\BackfillDemoSeeder', '--force', '--no-interaction'], {
    cwd: BACKEND_DIR,
    env: withEnv(BACKEND_ENV),
    stdio: 'pipe',
  })
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function openData(page: Page) {
  const backfill = page.waitForResponse((r) => r.url().includes('/api/data/backfill') && r.request().method() === 'GET')
  const storage = page.waitForResponse((r) => r.url().includes('/api/data/storage'))
  await page.goto('/settings/data')
  const report = ((await (await backfill).json()) as { data: Report }).data
  const sizes = ((await (await storage).json()) as { data: { total_bytes: number } }).data
  await expect(page.getByTestId('dt-summary')).toBeVisible()
  return { report, sizes }
}

/** Serves the real report with every job done, so the summary offers Re-sync now. */
async function routeAllDone(page: Page) {
  await page.route('**/api/data/backfill*', async (route) => {
    const res = await route.fetch()
    const body = (await res.json()) as { data: Report }
    for (const i of body.data.instruments)
      for (const j of i.datasets) {
        j.status = 'done'
        if (j.expected === null) j.expected = j.rows_written
      }
    await route.fulfill({ response: res, json: body })
  })
}

test('lists every seeded instrument with each dataset’s percent and status, and the storage total matches the API', async ({
  page,
  lang,
}, info) => {
  const s = locale(lang)
  const { report, sizes } = await openData(page)
  const ccy = new Map(report.instruments.filter((i) => !i.inst_id.includes('-')).map((i) => [i.inst_id, i]))
  const insts = report.instruments.filter((i) => i.inst_id.includes('-'))
  expect(insts.length).toBe(8)

  await expect(page.getByTestId('dt-row')).toHaveCount(insts.length)
  for (const inst of insts) {
    const row = page.locator(`[data-testid="dt-row"][data-inst="${inst.inst_id}"]`)
    await expect(row).toBeVisible()
    const jobs = [...inst.datasets]
    const oi = ccy.get(inst.inst_id.split('-')[0]!)?.datasets.find((d) => d.dataset === 'open_interest')
    if (oi) jobs.push(oi)
    for (const job of jobs) {
      const cell = row.locator(`td[data-ds="${job.dataset}"]`)
      const status = STATUS[job.status]!
      await expect(cell).toHaveAttribute('data-status', status)
      if (job.dataset.startsWith('candles:')) {
        await expect(cell.locator('.dt-cell-val [aria-hidden] .fig')).toHaveText(`${pct(job)}%`)
        await expect(cell.locator('.sr-only')).toHaveText(`${pct(job)}%, ${str(s, `st.${status}`)}`)
      }
    }
  }
  // XCU's 1H job failed: its row says so, and so does the summary's notice.
  await expect(page.locator('[data-testid="dt-row"][data-inst="XCU-USDT-SWAP"]')).toHaveAttribute('data-status', 'failed')
  await expect(page.getByTestId('dt-notice-failed')).toBeVisible()

  await expect(page.getByTestId('dt-storage-total')).toContainText(bytesText(sizes.total_bytes))

  // Percentages and instIds are left-to-right islands in both languages.
  const idDir = await page.locator('[data-testid="dt-row"] .inst-id').first().evaluate((el) => getComputedStyle(el).direction)
  expect(idDir).toBe('ltr')
  const figDir = await page.locator('[data-testid="dt-row"] .fig').first().evaluate((el) => getComputedStyle(el).direction)
  expect(figDir).toBe('ltr')
  await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')

  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-running.png` })
  for (const id of ['dt-quality', 'dt-keeps', 'dt-instruments'])
    await page.getByTestId(id).screenshot({ path: `${SHOTS}/${info.project.name}-${id}.png` })
})

test('Re-sync now posts, shows a busy state until the answer, and a 429 shows the try-again notice', async ({ page, lang }, info) => {
  const s = locale(lang)
  await routeAllDone(page)
  let release: () => void = () => {}
  const posts: { body: unknown }[] = []
  let answer = 202
  await page.route('**/api/data/resync', async (route: Route) => {
    posts.push({ body: route.request().postDataJSON() })
    if (answer === 202) {
      await new Promise<void>((r) => (release = r))
      await route.fulfill({ status: 202, json: { ok: true, env: 'demo', inst_id: null, trading_core: { ok: true } } })
    } else {
      await route.fulfill({ status: 429, json: { message: 'Too Many Attempts.' } })
    }
  })
  await openData(page)
  await expect(page.getByTestId('dt-summary')).toHaveAttribute('data-state', 'done')

  const button = page.getByTestId('dt-resync')
  await expect(button).toHaveText(str(s, 'resync'))
  await button.click()
  await expect(button).toHaveAttribute('aria-busy', 'true')
  await expect(button).toContainText(str(s, 'resyncing'))
  // The busy state shows before the request leaves (the CSRF cookie is fetched first).
  await expect.poll(() => posts.length).toBe(1)
  expect(posts[0]!.body).toEqual({ env: 'demo' })
  release()
  await expect(button).not.toHaveAttribute('aria-busy', 'true')
  await expect(button).toHaveText(str(s, 'resync'))
  await expect(page.getByText(str(s, 'toast.resync'), { exact: true })).toBeVisible()

  answer = 429
  await button.click()
  const notice = page.getByTestId('dt-notice-limited')
  await expect(notice).toBeVisible()
  await expect(notice).toContainText(str(s, 'limited.title'))
  await expect(notice).toContainText(str(s, 'limited.body'))
  await expect.poll(() => posts.length).toBe(2)

  mkdirSync(SHOTS, { recursive: true })
  await page.getByTestId('dt-summary').screenshot({ path: `${SHOTS}/${info.project.name}-429.png` })
})

test('a row re-sync posts its inst_id, and a 429 answers with the warning toast', async ({ page, lang }) => {
  const s = locale(lang)
  const posts: unknown[] = []
  await page.route('**/api/data/resync', async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ status: 429, json: { message: 'Too Many Attempts.' } })
  })
  await openData(page)
  const row = page.locator('[data-testid="dt-row"][data-inst="BTC-USDT-SWAP"]')
  await row.getByTestId('dt-row-resync').click()
  await expect(page.getByText(str(s, 'toast.limited'), { exact: true })).toBeVisible()
  await expect.poll(() => posts).toEqual([{ env: 'demo', inst_id: 'BTC-USDT-SWAP' }])
})

test('the empty state (no jobs) shows its message and Start backfill', async ({ page, lang }, info) => {
  const s = locale(lang)
  await page.route('**/api/data/backfill*', (route) =>
    route.fulfill({
      json: {
        data: {
          env: 'demo',
          as_of: new Date().toISOString(),
          instruments: [],
          totals: { instruments: 0, jobs: 0, by_status: { pending: 0, running: 0, done: 0, failed: 0, paused: 0 }, rows_written: 0, percent: null },
          quality: { open: 0, open_by_kind: { gap: 0, duplicate: 0, zero_volume: 0, outlier: 0 } },
          quality_issues: [],
        },
      },
    }),
  )
  await page.goto('/settings/data')
  const empty = page.getByTestId('dt-not-started')
  await expect(empty).toBeVisible()
  await expect(empty.getByRole('heading')).toHaveText(str(s, 'empty.title'))
  await expect(empty).toContainText(str(s, 'empty.body'))
  await expect(page.getByTestId('dt-start')).toHaveText(str(s, 'start'))
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-empty.png` })
})

test('the error state (API 500) shows its message and Try again', async ({ page, lang }, info) => {
  const s = locale(lang)
  await page.route('**/api/data/backfill*', (route) => route.fulfill({ status: 500, json: { message: 'Server Error' } }))
  await page.goto('/settings/data')
  const error = page.getByTestId('dt-error')
  await expect(error).toBeVisible({ timeout: 15_000 })
  await expect(error.getByRole('heading')).toHaveText(str(s, 'error.title'))
  await expect(error).toContainText(plain(str(s, 'error.body')))
  await expect(error.getByRole('button', { name: str(s, 'retry') })).toBeVisible()
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-error.png` })
})

test('a row opens the instrument sheet with its lanes and issues', async ({ page, lang }, info) => {
  const s = locale(lang)
  await openData(page)
  await page.locator('[data-testid="dt-row"][data-inst="XCU-USDT-SWAP"] .dt-inst-btn').click()
  const sheet = page.getByTestId('dt-sheet')
  await expect(sheet).toBeVisible()
  await expect(sheet.locator('.dt-lane-row')).toHaveCount(7)
  await expect(sheet.locator('.dt-lane-error')).toContainText(str(s, 'sh.error'))
  await expect(sheet.getByTestId('dt-issue')).toHaveCount(1)
  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-sheet.png` })
  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()
})
