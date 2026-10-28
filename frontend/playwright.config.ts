import { defineConfig, devices } from '@playwright/test'

// E2E_PORT lets parallel checkouts run side by side; 3000 is the owner's dev server.
const port = Number(process.env.E2E_PORT ?? 3140)

export default defineConfig({
  testDir: './e2e',
  outputDir: '../../evidence/playwright/test-results',
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npx vite --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
