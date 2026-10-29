import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { summarise, type ApiSource, type SourcesReport } from '../../src/features/settings-data-sources/model'
import { mixed } from '../../src/features/settings-data-sources/testFixtures'
import { EVIDENCE_DIR, FRONTEND_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-69: adding, replacing and removing a CryptoPanic/Finnhub key from Settings → Data sources, in
 * the four appearance projects (en/ar × light/dark). The owner is signed in against the real Laravel;
 * /api/data-sources and its PUT/DELETE /{key}/key are routed in the browser with ATL-66/71's shapes
 * (provider-wide writes), held in one in-memory list so a refetch after a change sees it.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-69')
const TYPED = 'cpQ7-test-Zk81wX1234'

type Strings = Record<string, unknown>
const locale = (lang: string): Strings =>
  JSON.parse(readFileSync(path.join(FRONTEND_DIR, 'src/i18n/locales', lang, 'settings-data-sources.json'), 'utf8')) as Strings
const str = (s: Strings, key: string, vars: Record<string, string> = {}): string =>
  Object.entries(vars).reduce(
    (out, [k, v]) => out.replace(`{{${k}}}`, v),
    key.split('.').reduce<unknown>((o, k) => (o as Strings)[k], s) as string,
  )

test.use({ viewport: { width: 1280, height: 900 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

interface KeyCall {
  method: string
  url: string
  body: unknown
}

/** Serves GET from `state` and applies PUT/DELETE provider-wide; `answer` forces a status for the next key call. */
async function serve(page: Page) {
  let list: ApiSource[] = mixed(Date.now()).data
  const calls: KeyCall[] = []
  const ctl = { answer: 200 }
  const report = (): SourcesReport => ({ data: list, summary: summarise(list) })
  await page.route('**/api/data-sources', (route) => route.fulfill({ json: report() }))
  await page.route('**/api/data-sources/*/key', async (route) => {
    const req = route.request()
    const key = decodeURIComponent(new URL(req.url()).pathname.split('/')[3]!)
    calls.push({ method: req.method(), url: new URL(req.url()).pathname, body: req.method() === 'PUT' ? req.postDataJSON() : null })
    if (ctl.answer === 422) {
      await route.fulfill({ status: 422, json: { message: 'The api key field format is invalid.', errors: { api_key: ['The api key field format is invalid.'] } } })
      return
    }
    if (ctl.answer !== 200) {
      await route.fulfill({ status: ctl.answer, json: { message: ctl.answer === 429 ? 'Too Many Attempts.' : 'Server Error' } })
      return
    }
    const provider = list.find((s) => s.key === key)!.provider
    const owned = new Set(list.filter((s) => s.requires_key && s.provider === provider).map((s) => s.key))
    if (req.method() === 'PUT') {
      const last4 = String((req.postDataJSON() as { api_key: string }).api_key).slice(-4)
      list = list.map((s) =>
        owned.has(s.key)
          ? { ...s, has_key: true, key_last4: last4, status: 'never_run', consecutive_failures: 0, disabled_until: null, last_error: null, last_success_at: null }
          : s.key === 'forexfactory.calendar' && provider === 'finnhub'
            ? { ...s, covered_by: 'finnhub.calendar' }
            : s,
      )
      await route.fulfill({ json: { data: list.find((s) => s.key === key) } })
      return
    }
    list = list.map((s) =>
      owned.has(s.key)
        ? { ...s, has_key: false, key_last4: null, status: 'needs_key' }
        : s.covered_by && owned.has(s.covered_by)
          ? { ...s, covered_by: null }
          : s,
    )
    await route.fulfill({ status: 204, body: '' })
  })
  return { calls, ctl }
}

async function open(page: Page) {
  await page.goto('/settings/sources')
  await expect(page.getByTestId('src-summary')).toBeVisible()
}

const row = (page: Page, key: string) => page.locator(`[data-testid="src-row"][data-key="${key}"]`)
const keyRow = (page: Page, provider: string) => page.locator(`[data-testid="src-key-row"][data-provider="${provider}"]`)

/** The typed key must be nowhere the page keeps things once the sheet is closed. */
async function expectNoLeak(page: Page, secret: string) {
  const stores = await page.evaluate(() => {
    const dump = (st: Storage) => Array.from({ length: st.length }, (_, i) => `${st.key(i)}=${st.getItem(st.key(i)!)}`).join('\n')
    return { local: dump(localStorage), session: dump(sessionStorage) }
  })
  expect(stores.local).not.toContain(secret)
  expect(stores.session).not.toContain(secret)
  expect(await page.content()).not.toContain(secret)
  const inputs = await page.evaluate(() => Array.from(document.querySelectorAll('input')).map((i) => i.value))
  expect(inputs).not.toContain(secret)
}

test('adding a key sends PUT with the typed value, shows the last four, and the row leaves Needs a key', async ({ page, lang }, info) => {
  const s = locale(lang)
  const { calls } = await serve(page)
  await open(page)

  const cp = row(page, 'cryptopanic')
  await expect(cp).toHaveAttribute('data-status', 'needs_key')
  await cp.getByTestId('src-add-key').click()

  const sheet = page.getByRole('dialog')
  await expect(sheet).toBeVisible()
  await expect(sheet.getByRole('heading')).toHaveText(str(s, 'ks.addTitle', { provider: 'CryptoPanic' }))
  const link = sheet.getByRole('link', { name: str(s, 'ks.open', { site: 'cryptopanic.com' }) })
  await expect(link).toHaveAttribute('href', 'https://cryptopanic.com/developers/api/')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(link).toHaveAttribute('target', '_blank')
  const field = sheet.getByLabel(str(s, 'ks.field'), { exact: true })
  await expect(field).toBeFocused()
  await expect(field).toHaveAttribute('type', 'password')
  const save = sheet.getByTestId('src-ks-save')
  await expect(save).toBeDisabled()
  await field.fill('   ')
  await expect(save).toBeDisabled()
  await field.fill(TYPED)
  await expect(save).toBeEnabled()
  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-sheet.png` })
  await save.click()

  await expect.poll(() => calls).toEqual([{ method: 'PUT', url: '/api/data-sources/cryptopanic/key', body: { api_key: TYPED } }])
  await expect(sheet).toBeHidden()
  await expect(cp).not.toHaveAttribute('data-status', 'needs_key')
  await expect(cp).toHaveAttribute('data-status', /starting|never_run/)
  await expect(cp.getByRole('switch')).toBeEnabled()
  const mask = keyRow(page, 'cryptopanic').getByTestId('src-key-mask')
  await expect(mask).toContainText('•••• 1234')
  await expect(mask).toHaveAttribute('aria-label', str(s, 'keys.masked', { last4: '1234' }))
  await expect(keyRow(page, 'cryptopanic').locator('[data-key-action="replace"]')).toBeFocused()
  await expect(page.getByText(str(s, 'ks.saved', { provider: 'CryptoPanic' }), { exact: true })).toBeVisible()

  await expectNoLeak(page, TYPED)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-saved.png`, fullPage: true })
})

test('removing a key sends DELETE after confirming and the rows return to Needs a key; Cancel sends nothing', async ({ page, lang }, info) => {
  const s = locale(lang)
  const { calls } = await serve(page)
  await open(page)

  const fh = keyRow(page, 'finnhub')
  await expect(fh.getByTestId('src-key-mask')).toContainText('7Q2F')
  await expect(row(page, 'forexfactory.calendar')).toHaveAttribute('data-status', 'standby')

  await fh.locator('[data-key-action="remove"]').click()
  const sheet = page.getByRole('dialog')
  await expect(sheet.getByRole('heading')).toHaveText(str(s, 'rm.title', { provider: 'Finnhub' }))
  await expect(sheet.getByTestId('src-rm-cancel')).toBeFocused()
  await sheet.getByTestId('src-rm-cancel').click()
  await expect(sheet).toBeHidden()
  await page.waitForTimeout(300)
  expect(calls).toEqual([])
  await expect(row(page, 'finnhub.calendar')).not.toHaveAttribute('data-status', 'needs_key')

  await fh.locator('[data-key-action="remove"]').click()
  await expect(sheet).toBeVisible()
  await page.waitForTimeout(400)
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-remove.png` })
  await sheet.getByTestId('src-rm-do').click()

  await expect.poll(() => calls).toEqual([{ method: 'DELETE', url: '/api/data-sources/finnhub.calendar/key', body: null }])
  await expect(sheet).toBeHidden()
  await expect(row(page, 'finnhub.calendar')).toHaveAttribute('data-status', 'needs_key')
  await expect(row(page, 'finnhub.calendar').getByTestId('src-status')).toHaveText(str(s, 'st.needs_key'))
  await expect(row(page, 'forexfactory.calendar')).not.toHaveAttribute('data-status', 'standby')
  await expect(fh.locator('[data-key-action="add"]')).toBeFocused()
  await expect(fh.getByTestId('src-key-mask')).toHaveCount(0)
  await expect(page.getByText(str(s, 'rm.toast', { provider: 'Finnhub' }), { exact: true })).toBeVisible()
})

test('a 422 shows the field error and keeps the sheet open; a 429 says try again in a minute', async ({ page, lang }, info) => {
  const s = locale(lang)
  const { calls, ctl } = await serve(page)
  await open(page)

  ctl.answer = 422
  await keyRow(page, 'finnhub').locator('[data-key-action="replace"]').click()
  const sheet = page.getByRole('dialog')
  await expect(sheet.getByRole('heading')).toHaveText(str(s, 'ks.replaceTitle', { provider: 'Finnhub' }))
  const field = sheet.getByLabel(str(s, 'ks.field'), { exact: true })
  await field.fill('bad')
  await sheet.getByTestId('src-ks-save').click()

  await expect(sheet.getByText(str(s, 'ks.invalid', { provider: 'Finnhub' }))).toBeVisible()
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  await expect(field).toHaveValue('bad')
  await expect(field).toBeFocused()
  await expect(sheet).toBeVisible()
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-422.png` })

  ctl.answer = 429
  await field.fill(TYPED)
  await expect(field).not.toHaveAttribute('aria-invalid', 'true')
  await sheet.getByTestId('src-ks-save').click()
  const notice = sheet.getByTestId('src-ks-notice')
  await expect(notice).toHaveAttribute('role', 'alert')
  await expect(notice).toContainText(str(s, 'ks.limited.title'))
  await expect(notice).toContainText(str(s, 'ks.limited.body'))
  await expect(sheet.getByTestId('src-ks-save')).toBeEnabled()
  await expect(sheet).toBeVisible()
  expect(calls.map((c) => c.method)).toEqual(['PUT', 'PUT'])
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-429.png` })

  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()
  await expectNoLeak(page, TYPED)
  // Nothing changed on the page: the key row still shows the old key.
  await expect(keyRow(page, 'finnhub').getByTestId('src-key-mask')).toContainText('7Q2F')
})

test('the key sheet passes axe and works by keyboard alone (Tab, Enter, Esc), and leaves no trace of the key', async ({ page }) => {
  const { calls } = await serve(page)
  await open(page)

  const add = keyRow(page, 'cryptopanic').locator('[data-key-action="add"]')
  await add.focus()
  await page.keyboard.press('Enter')
  const sheet = page.getByRole('dialog')
  await expect(sheet).toBeVisible()
  const field = sheet.locator('#src-ks-input')
  await expect(field).toBeFocused()
  await page.waitForTimeout(400)
  const axe = await new AxeBuilder({ page }).include('[role="dialog"]').analyze()
  const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(serious.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')}`))).toEqual([])

  // Esc closes with what was typed thrown away; reopening starts empty.
  await page.keyboard.type(TYPED)
  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()
  await expect(add).toBeFocused()
  await expectNoLeak(page, TYPED)
  expect(calls).toEqual([])

  await page.keyboard.press('Enter')
  await expect(field).toBeFocused()
  await expect(field).toHaveValue('')
  // Tab walks the sheet and stays inside it: the reveal button follows the field.
  await page.keyboard.type(TYPED)
  await page.keyboard.press('Tab')
  const reveal = sheet.locator('#src-ks-input ~ div button')
  await expect(reveal).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(field).toHaveAttribute('type', 'text')
  for (let i = 0; i < 6; i++) await page.keyboard.press('Tab')
  expect(await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))).toBe(true)
  await field.focus()
  await page.keyboard.press('Enter') // Return submits
  await expect.poll(() => calls.length).toBe(1)
  await expect(sheet).toBeHidden()
  await expectNoLeak(page, TYPED)
})
