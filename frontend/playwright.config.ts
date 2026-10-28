import { defineConfig, devices } from '@playwright/test'
import type { AppearanceOptions } from './e2e/flows/appearance'
import { BACKEND_DIR, BACKEND_ENV, CORE_ENV, CORE_URL, API_URL, PORTS, SERVICES_DIR, SPA_URL, STORAGE_STATE, withEnv } from './e2e/support/stack'

/**
 * Two suites:
 * - the mocked specs (e2e/*.spec.ts) fake Laravel with page.route on their own Vite (E2E_PORT);
 * - the recorded flows (e2e/flows/, ATL-25) run against a real Laravel + trading_core in OKX fixture
 *   mode on their own ports and database; see e2e/support/stack.ts and CLAUDE.md "Recorded flows".
 */
const desktop = { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } }

const appearance = [
  { name: 'ltr-light', lang: 'en', theme: 'light' },
  { name: 'ltr-dark', lang: 'en', theme: 'dark' },
  { name: 'rtl-light', lang: 'ar', theme: 'light' },
  { name: 'rtl-dark', lang: 'ar', theme: 'dark' },
] as const

export default defineConfig<AppearanceOptions>({
  testDir: './e2e',
  outputDir: '../../evidence/playwright/test-results',
  reporter: [['list']],
  projects: [
    {
      name: 'chromium',
      testIgnore: /flows\//,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${PORTS.mocked}` },
    },
    // Seeds the e2e database and signs the test owner in once.
    { name: 'setup', testMatch: /flows\/auth\.setup\.ts/, use: { ...desktop, baseURL: SPA_URL } },
    {
      name: 'onboarding',
      testMatch: /flows\/onboarding\.spec\.ts/,
      dependencies: ['setup'],
      use: { ...desktop, baseURL: SPA_URL, storageState: STORAGE_STATE },
    },
    // Everything below needs the owner onboarded, so it runs after the onboarding flow.
    {
      name: 'settings',
      testMatch: /flows\/settings\.spec\.ts/,
      dependencies: ['onboarding'],
      use: { ...desktop, baseURL: SPA_URL, storageState: STORAGE_STATE },
    },
    ...appearance.map((a) => ({
      name: a.name,
      testMatch: /flows\/.*\.appearance\.spec\.ts/,
      dependencies: ['onboarding'],
      use: { ...desktop, baseURL: SPA_URL, storageState: STORAGE_STATE, lang: a.lang, theme: a.theme },
    })),
  ],
  webServer: [
    {
      command: `npx vite --port ${PORTS.mocked} --strictPort`,
      url: `http://127.0.0.1:${PORTS.mocked}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${PORTS.spa} --strictPort`,
      url: SPA_URL,
      env: withEnv({ VITE_API_URL: API_URL }),
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // php -S directly (not `artisan serve`, which strips the environment it passes on).
      // Laravel's router script serves from the working directory, so it runs inside public/.
      command: `php -S 127.0.0.1:${PORTS.api} ../vendor/laravel/framework/src/Illuminate/Foundation/resources/server.php`,
      cwd: `${BACKEND_DIR}/public`,
      url: `${API_URL}/up`,
      env: withEnv(BACKEND_ENV),
      // php -S logs every connection to stderr; Laravel's own log is backend/storage/logs/laravel.log.
      stderr: 'ignore',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'uv run --package atlas-trading-core python -m trading_core',
      cwd: SERVICES_DIR,
      url: `${CORE_URL}/health`,
      env: withEnv(CORE_ENV),
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
