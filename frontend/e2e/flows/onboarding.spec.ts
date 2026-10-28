import { expect, test, type Page } from '@playwright/test'
import { API_URL, SHOTS, SPA_URL } from '../support/stack'

/**
 * Brief §19 onboarding flow on the real stack (ATL-25): a new owner walks Welcome → Connect (a
 * fixture key, tested through Laravel → trading_core → recorded OKX JSON) → Risk Conservative →
 * Autonomy 2 and lands on Portfolio. The key below is made up; fixture mode never calls OKX.
 */
const FIXTURE_KEY = { key: 'e2e00000-0000-4000-8000-00000000f1x7', secret: 'E2E-FIXTURE-SECRET', pass: 'e2e-fixture-pass' }

/** Laravel's JSON API as the signed-in SPA sees it (Sanctum needs the SPA origin). */
async function apiGet<T>(page: Page, path: string): Promise<T> {
  const res = await page.request.get(`${API_URL}${path}`, { headers: { Accept: 'application/json', Origin: SPA_URL, Referer: `${SPA_URL}/` } })
  expect(res.status(), `${path} status`).toBe(200)
  return (await res.json()) as T
}

type Connection = { env: string; last_verified_at: string | null; permissions: string[] | null; api_key_last4: string; account_mode: string | null }
type SettingsDoc = { data: { version: number; settings: Record<string, unknown> } }

test('a new owner goes from Welcome to Portfolio through Connect, Risk and Autonomy', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'en')
    localStorage.setItem('atlas.theme', 'light')
  })
  await page.goto('/')
  await expect(page).toHaveURL(/\/onboarding$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Welcome to Atlas')
  await page.getByTestId('ob-next').click()

  // Connect: save the key and test it for real through Laravel and trading_core.
  await expect(page).toHaveURL(/step=connect/)
  await expect(page.getByTestId('ob-next')).toBeDisabled()
  await page.getByLabel('API key', { exact: true }).fill(FIXTURE_KEY.key)
  await page.getByLabel('Secret key', { exact: true }).fill(FIXTURE_KEY.secret)
  await page.getByLabel('Passphrase', { exact: true }).fill(FIXTURE_KEY.pass)
  await page.getByRole('button', { name: 'Test connection' }).click()

  const success = page.getByTestId('test-success')
  await expect(success).toBeVisible({ timeout: 15_000 })
  // The recorded account_config has a zeroed UID and a key with Withdraw: demo accepts it with a warning.
  await expect(page.getByTestId('uid-tail')).toHaveText('•••• 0000')
  await expect(page.getByTestId('withdraw-warning')).toContainText('This key can withdraw funds')
  await expect(page.getByTestId('saved-key')).toContainText('•••• f1x7')
  await expect(page.getByTestId('ob-next')).toBeEnabled()
  await page.waitForTimeout(600) // the result card settles in before the picture
  await page.screenshot({ path: `${SHOTS}/onboarding-connect-tested.png`, fullPage: true })

  const { data: connections } = await apiGet<{ data: Connection[] }>(page, '/api/connections')
  expect(connections).toHaveLength(1)
  expect(connections[0]).toMatchObject({ env: 'demo', api_key_last4: 'f1x7' })
  expect(connections[0].last_verified_at).not.toBeNull()
  expect(connections[0].permissions).toEqual(expect.arrayContaining(['read_only', 'trade', 'withdraw']))

  // Risk: Balanced is preselected; choose Conservative.
  await page.getByTestId('ob-next').click()
  await expect(page).toHaveURL(/step=risk$/)
  const profiles = page.getByRole('radiogroup', { name: 'Risk profile' })
  await expect(profiles.getByRole('radio', { name: 'Balanced' })).toHaveAttribute('aria-checked', 'true')
  await profiles.getByRole('radio', { name: 'Conservative' }).click()
  await expect(profiles.getByRole('radio', { name: 'Conservative' })).toHaveAttribute('aria-checked', 'true')
  await page.getByTestId('ob-next').click()

  // Autonomy: level 2 (approve each trade) is the default; keep it.
  await expect(page).toHaveURL(/step=autonomy$/)
  const levels = page.getByRole('radiogroup', { name: 'Autonomy' })
  await expect(levels.getByRole('radio', { checked: true })).toHaveAttribute('data-value', '2')
  await page.getByTestId('ob-next').click()

  await expect(page).toHaveURL(/\/portfolio$/)
  await expect(page.getByTestId('hero-equity')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/onboarding-landed-portfolio.png`, fullPage: true })

  // The choices are saved in Laravel's settings, not just in the page.
  const { data: doc } = await apiGet<SettingsDoc>(page, '/api/settings?env=demo')
  expect(doc.settings).toMatchObject({ risk_profile: 'conservative', max_leverage: 2, autonomy_level: 2, onboarding_completed: 1 })
  expect(doc.version).toBeGreaterThan(1)

  // Settings → Risk shows the saved profile, and onboarding no longer reopens.
  await page.goto('/settings/risk')
  await expect(page.getByRole('radiogroup', { name: 'Risk profile' }).getByRole('radio', { name: 'Conservative' })).toHaveAttribute('aria-checked', 'true')
  await page.goto('/')
  await expect(page).toHaveURL(/\/portfolio$/)
})
