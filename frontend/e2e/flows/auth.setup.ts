import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { expect, test as setup } from '@playwright/test'
import { BACKEND_DIR, BACKEND_ENV, DATABASE, OWNER, STORAGE_STATE, withEnv } from '../support/stack'

/**
 * Resets the flows' database and signs the test owner in once (ATL-25). The owner starts with no
 * connection and untouched settings, so onboarding.spec walks the whole wizard; PortfolioDemoSeeder
 * gives the Portfolio pages their figures.
 */
setup('seed the e2e database and sign the owner in', async ({ page }) => {
  setup.setTimeout(180_000)
  if (!DATABASE.startsWith('atlas_e2e_')) throw new Error(`refusing to reset ${DATABASE}`)
  const env = withEnv(BACKEND_ENV)
  const run = (args: string[]) => execFileSync('php', args, { cwd: BACKEND_DIR, env, stdio: 'pipe' }).toString()
  run(['tests/E2e/database.php'])
  run(['artisan', 'migrate:fresh', '--force', '--no-interaction'])
  run(['artisan', 'db:seed', '--class=Database\\Seeders\\E2eSeeder', '--force', '--no-interaction'])

  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'en'))
  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill(OWNER.email)
  await page.getByLabel('Password', { exact: true }).fill(OWNER.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  // A signed-in owner who has not onboarded is sent to the wizard.
  await expect(page).toHaveURL(/\/onboarding/)
  mkdirSync(path.dirname(STORAGE_STATE), { recursive: true })
  await page.context().storageState({ path: STORAGE_STATE })
})
