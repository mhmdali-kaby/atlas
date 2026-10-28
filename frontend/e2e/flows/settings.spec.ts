import { expect, test } from '@playwright/test'
import { API_URL, SHOTS, SPA_URL } from '../support/stack'

/**
 * Brief §19 "edit settings" on the real stack (ATL-25): the onboarded owner (Conservative, 2×)
 * raises max leverage on Settings → Risk, and the Activity list shows the new version Laravel saved.
 */
type SettingsDoc = { data: { version: number; settings: Record<string, unknown> } }

test('changing max leverage saves a new version that Activity shows', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'en')
    localStorage.setItem('atlas.theme', 'light')
  })
  const read = async () => {
    const res = await page.request.get(`${API_URL}/api/settings?env=demo`, { headers: { Accept: 'application/json', Origin: SPA_URL, Referer: `${SPA_URL}/` } })
    expect(res.status()).toBe(200)
    return ((await res.json()) as SettingsDoc).data
  }
  const before = await read()
  const from = Number(before.settings.max_leverage)
  const to = from + 1

  await page.goto('/settings/risk')
  const leverage = page.getByRole('textbox', { name: 'Max leverage', exact: true })
  await expect(leverage).toHaveValue(String(from))
  await expect(page.locator('[data-testid="activity"] li').first()).toHaveAttribute('data-version', String(before.version))

  await leverage.fill(String(to))
  await expect(page.getByRole('radiogroup', { name: 'Risk profile' }).getByRole('radio', { name: 'Custom' })).toHaveAttribute('aria-checked', 'true')
  await page.getByRole('button', { name: 'Save changes' }).click()

  const newest = page.locator('[data-testid="activity"] li').first()
  await expect(newest).toHaveAttribute('data-version', String(before.version + 1))
  await expect(newest).toContainText(`Max leverage ${from}× → ${to}×`)
  await expect(page.getByTestId('settings-version')).toContainText(String(before.version + 1))
  await expect(page.getByTestId('save-bar')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/settings-risk-new-version.png`, fullPage: true })

  const after = await read()
  expect(after.version).toBe(before.version + 1)
  expect(after.settings).toMatchObject({ max_leverage: to, risk_profile: 'custom' })

  // A reload reads the saved version back from Laravel.
  await page.reload()
  await expect(leverage).toHaveValue(String(to))
  await expect(page.locator('[data-testid="activity"] li').first()).toHaveAttribute('data-version', String(after.version))
})
