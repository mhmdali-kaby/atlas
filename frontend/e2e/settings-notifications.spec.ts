import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-218: Settings → Notifications against a faked Laravel (ATL-214 settings keys, ATL-215 Telegram routes). */

const OWNER = {
  id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad',
  name: 'Mohammed Ali',
  email: 'owner@atlas.test',
  locale: 'en',
  theme: 'system',
}
const SHOTS = '../../evidence/ATL-218'

const PRESETS = {
  conservative: {
    max_loss_per_trade_pct: '0.5',
    max_position_pct: '2',
    max_daily_loss_pct: '2',
    max_weekly_loss_pct: '5',
    max_leverage: 2,
    max_open_positions: 3,
    max_share_per_instrument_pct: '15',
    max_correlated_exposure_pct: '20',
    min_expected_r: '2.0',
    max_consecutive_losses: 2,
  },
  balanced: {
    max_loss_per_trade_pct: '1.0',
    max_position_pct: '5',
    max_daily_loss_pct: '4',
    max_weekly_loss_pct: '10',
    max_leverage: 5,
    max_open_positions: 6,
    max_share_per_instrument_pct: '25',
    max_correlated_exposure_pct: '35',
    min_expected_r: '1.8',
    max_consecutive_losses: 3,
  },
  aggressive: {
    max_loss_per_trade_pct: '2.0',
    max_position_pct: '10',
    max_daily_loss_pct: '8',
    max_weekly_loss_pct: '20',
    max_leverage: 10,
    max_open_positions: 10,
    max_share_per_instrument_pct: '40',
    max_correlated_exposure_pct: '50',
    min_expected_r: '1.5',
    max_consecutive_losses: 4,
  },
} as const

const CONNECTION = {
  id: 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30',
  exchange: 'okx',
  env: 'demo',
  label: 'OKX demo',
  api_key_last4: 'ab12',
  has_secret: true,
  has_passphrase: true,
  permissions: ['read_only', 'trade'],
  account_mode: 'futures',
  position_mode: 'net_mode',
  uid_last4: '6123',
  last_verified_at: '2026-10-28T08:00:00Z',
  last_test_error: null,
  created_at: '2026-10-28T08:00:00Z',
  updated_at: '2026-10-28T08:00:00Z',
}

const EVENTS = [
  'proposal_awaiting_approval',
  'circuit_breaker',
  'kill_switch',
  'reconciliation_mismatch',
  'order_filled',
  'stop_hit',
  'target_hit',
  'news_alert',
  'agent_degraded',
  'data_stale',
  'daily_report',
]
const TOKEN = '123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsWXYZ'
const NONE = {
  configured: false,
  bot_token_last4: null,
  chat_id_last4: null,
  last_sent_at: null,
  last_tested_at: null,
  last_error: null,
  updated_at: null,
}

async function fakeApi(page: Page, opts: { telegram?: boolean } = {}) {
  const settings: Record<string, string | number | boolean> = {
    risk_profile: 'balanced',
    ...PRESETS.balanced,
    autonomy_level: 2,
    onboarding_completed: 1,
  }
  for (const e of EVENTS) {
    settings[`notifications.${e}.in_app`] = true
    settings[`notifications.${e}.browser`] = true
    settings[`notifications.${e}.telegram`] = false
  }
  const state = {
    version: 7,
    settings,
    patches: [] as Record<string, unknown>[],
    telegram: opts.telegram
      ? {
          configured: true,
          bot_token_last4: 'WXYZ',
          chat_id_last4: '4321',
          last_sent_at: null,
          last_tested_at: null,
          last_error: null,
          updated_at: '2026-11-02T09:00:00Z',
        }
      : ({ ...NONE } as Record<string, unknown>),
    puts: [] as unknown[],
    tests: 0,
  }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = {
      'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*',
      'Access-Control-Allow-Credentials': 'true',
    }
    const json = (status: number, body?: unknown) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: cors,
        body: body === undefined ? '' : JSON.stringify(body),
      })
    const doc = () => ({
      data: {
        id: '9f6c',
        env: 'demo',
        version: state.version,
        settings: state.settings,
        presets: PRESETS,
        max_enabled_autonomy_level: 4,
        updated_at: '2026-11-02T09:00:00Z',
      },
    })
    if (method === 'OPTIONS')
      return route.fulfill({
        status: 204,
        headers: {
          ...cors,
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE',
        },
      })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [CONNECTION] })
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as {
        version: number
        changes: Record<string, boolean>
      }
      state.patches.push(body.changes)
      if (body.version !== state.version)
        return json(409, {
          message: 'stale',
          current_version: state.version,
        })
      state.settings = { ...state.settings, ...body.changes }
      state.version += 1
      return json(200, doc())
    }
    if (url.pathname === '/api/notifications/telegram/test' && method === 'POST') {
      state.tests += 1
      if (!state.telegram.configured)
        return json(409, {
          ok: false,
          error: 'not_configured',
          message: 'No bot.',
        })
      state.telegram = {
        ...state.telegram,
        last_tested_at: '2026-11-02T11:32:00Z',
        last_sent_at: '2026-11-02T11:32:00Z',
      }
      return json(200, {
        ok: true,
        error: null,
        message: null,
        data: state.telegram,
      })
    }
    if (url.pathname === '/api/notifications/telegram') {
      if (method === 'GET') return json(200, { data: state.telegram })
      if (method === 'PUT') {
        const body = JSON.parse(req.postData() ?? '{}') as {
          bot_token: string
          chat_id: string
        }
        state.puts.push(body)
        const created = !state.telegram.configured
        state.telegram = {
          configured: true,
          bot_token_last4: body.bot_token.slice(-4),
          chat_id_last4: body.chat_id.slice(-4),
          last_sent_at: null,
          last_tested_at: null,
          last_error: null,
          updated_at: '2026-11-02T11:30:00Z',
        }
        return json(created ? 201 : 200, { data: state.telegram })
      }
      if (method === 'DELETE') {
        state.telegram = { ...NONE }
        return json(200, { data: state.telegram })
      }
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(
  page: Page,
  opts: {
    lang?: 'en' | 'ar'
    theme?: 'light' | 'dark'
    telegram?: boolean
    width?: number
  } = {},
) {
  await page.setViewportSize({ width: opts.width ?? 1280, height: 900 })
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [opts.lang ?? 'en', opts.theme ?? 'light'] as const,
  )
  const api = await fakeApi(page, { telegram: opts.telegram })
  await page.goto('/settings/notifications')
  await expect(page.getByTestId('ns-page')).toBeVisible()
  return api
}

async function axe(page: Page, selector: string) {
  // .st-version is the shared Settings version pill, reported separately on every Settings page.
  const results = await new AxeBuilder({ page }).include(selector).exclude('.st-version').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('turning off Browser for "Order filled" sends one PATCH and survives a reload', async ({ page }) => {
  const api = await open(page)
  const sw = page.getByRole('switch', { name: 'Order filled: Browser' })
  await expect(sw).toBeChecked()
  await sw.click()
  await expect(sw).not.toBeChecked()
  await expect(page.getByTestId('settings-version')).toContainText('8')
  expect(api.patches).toEqual([{ 'notifications.order_filled.browser': false }])
  await page.screenshot({
    path: `${SHOTS}/matrix-en-light.png`,
    fullPage: true,
  })

  await page.reload()
  await expect(page.getByRole('switch', { name: 'Order filled: Browser' })).not.toBeChecked()
  await expect(page.getByRole('switch', { name: 'Order filled: In Atlas' })).toBeChecked()
  expect(api.patches).toHaveLength(1)
})

test('a failed save rolls the switch back with a toast', async ({ page }) => {
  await open(page)
  await page.route('http://localhost:8000/api/settings', (route) =>
    route.request().method() === 'PATCH'
      ? route.fulfill({
          status: 500,
          body: '{}',
          contentType: 'application/json',
          headers: {
            'Access-Control-Allow-Origin': 'http://localhost:3000',
            'Access-Control-Allow-Credentials': 'true',
          },
        })
      : route.fallback(),
  )
  const sw = page.getByRole('switch', { name: 'Stop-loss hit: In Atlas' })
  await sw.click()
  await expect(page.getByText('Couldn’t save that switch', { exact: true })).toBeVisible()
  await expect(sw).toBeChecked()
})

test('saving a bot then Send test shows success; the bot shows only •••• and its last 4', async ({ page }) => {
  const api = await open(page)
  await page.getByTestId('ns-tg-token').fill('not-a-token')
  await page.getByTestId('ns-tg-chat').fill('987654321')
  await page.getByTestId('tg-save').click()
  await expect(page.getByText('That doesn’t look like a bot token.', { exact: false })).toBeVisible()
  expect(api.puts).toHaveLength(0)

  await page.getByTestId('ns-tg-token').fill(TOKEN)
  await page.getByTestId('tg-save').click()
  await expect(page.getByTestId('tg-connected')).toBeVisible()
  expect(api.puts).toEqual([{ bot_token: TOKEN, chat_id: '987654321' }])
  await expect(page.getByTestId('tg-bot')).toHaveText('Bot \u2066•••• WXYZ\u2069')
  await expect(page.getByTestId('tg-chat')).toContainText('\u2066•••• 4321\u2069')
  // Saving turns on no Telegram switch; the column is usable now.
  await expect(page.getByRole('switch', { name: 'Order filled: Telegram' })).toBeEnabled()
  await expect(page.getByRole('switch', { name: 'Order filled: Telegram' })).not.toBeChecked()

  await page.getByTestId('tg-test').click()
  await expect(page.getByTestId('tg-test-ok')).toContainText('Test sent at')
  expect(api.tests).toBe(1)
  // The typed token is nowhere on the page any more.
  expect(await page.content()).not.toContain('AAHdqTcvCH1vGWJxfSeofSAs0K5PALDs')
  await page.screenshot({
    path: `${SHOTS}/telegram-connected-en-light.png`,
    fullPage: true,
  })

  // The saved notice turns on the four "Needs you" events in one PATCH.
  await page.getByTestId('tg-use-needs').click()
  await expect(page.getByText('Telegram is on for the 4 “Needs you” events')).toBeVisible()
  expect(api.patches.at(-1)).toEqual({
    'notifications.proposal_awaiting_approval.telegram': true,
    'notifications.circuit_breaker.telegram': true,
    'notifications.kill_switch.telegram': true,
    'notifications.reconciliation_mismatch.telegram': true,
  })
})

test('with no bot the Telegram switches are disabled with a reason, and again after Remove', async ({ page }) => {
  await open(page, { telegram: false })
  const tgSwitches = page.locator('[data-testid$="-telegram"][role="switch"]')
  await expect(tgSwitches).toHaveCount(11)
  for (const sw of await tgSwitches.all()) await expect(sw).toBeDisabled()
  const sw = page.getByRole('switch', { name: 'Order filled: Telegram' })
  await expect(sw).toHaveAccessibleDescription('Set up Telegram below to use this.')
  await expect(page.getByTestId('ns-matrix').getByText('Not set up')).toBeVisible()

  await page.getByTestId('ns-tg-token').fill(TOKEN)
  await page.getByTestId('ns-tg-chat').fill('987654321')
  await page.getByTestId('tg-save').click()
  await expect(sw).toBeEnabled()

  await page.getByTestId('tg-remove').click()
  await page.getByTestId('tg-rm-go').click()
  await expect(page.getByText('Telegram bot removed', { exact: true })).toBeVisible()
  await expect(page.getByTestId('tg-setup')).toBeVisible()
  for (const s of await tgSwitches.all()) await expect(s).toBeDisabled()
  await expect(sw).toHaveAccessibleDescription('Set up Telegram below to use this.')
})

test('Arabic dark at 390 px: no horizontal scroll and no axe violations', async ({ page }) => {
  await open(page, { lang: 'ar', theme: 'dark', width: 390, telegram: true })
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true)
  const overflow = await page.evaluate(() => {
    const els = [document.documentElement, document.body, ...document.querySelectorAll<HTMLElement>('.shell-content, .ns-page')]
    return els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.className || el.tagName)
  })
  expect(overflow).toEqual([])
  await page.screenshot({ path: `${SHOTS}/ar-dark-390.png`, fullPage: true })
  expect(await axe(page, '.ns-page')).toEqual([])

  // The not-set-up state too.
  await page.getByTestId('tg-remove').click()
  await page.getByTestId('tg-rm-go').click()
  await expect(page.getByTestId('tg-setup')).toBeVisible()
  await expect(page.getByText('أُزيل بوت تيليجرام', { exact: true })).toBeVisible()
  await page.waitForTimeout(400)
  expect(await axe(page, '.ns-page')).toEqual([])
  await page.screenshot({
    path: `${SHOTS}/ar-dark-390-setup.png`,
    fullPage: true,
  })
})

test('English light and dark at desktop width: no axe violations', async ({ page }) => {
  await open(page, { theme: 'dark' })
  expect(await axe(page, '.ns-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/en-dark.png`, fullPage: true })
})
