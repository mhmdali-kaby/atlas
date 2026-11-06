/// <reference types="vitest/config" />
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { storybookTest } from '@storybook/addon-vitest/vitest-plugin'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { playwright } from '@vitest/browser-playwright'
import { defineConfig, type Plugin } from 'vite'

const dirname = path.dirname(fileURLToPath(import.meta.url))

/*
 * ATL-193: Vitest's fsModuleCache (on by default, node_modules/.vitest-cache)
 * persists each module's transformed code across separate `vitest` process
 * runs, keyed on that module's own id + source text. It means to skip caching
 * any module that calls `import.meta.glob`, since the result also depends on
 * which files exist on disk — but its built-in check is a literal
 * `import.meta.glob(` substring match, and every glob in this app
 * (src/i18n/index.ts's locale namespaces, src/app/registry.ts's feature
 * routes) carries a TypeScript generic between `glob` and `(`, so the check
 * never fires and the stale file list is written to the cache anyway. A
 * checkout that already ran Vitest once, then gains a new locale namespace
 * or feature, replays the old glob result and renders raw translation keys
 * (or drops the new routes) until someone runs `vitest --clearCache` by
 * hand. This plugin makes Vitest bail out of caching those modules the way
 * it already means to for the literal form, so no manual step is needed.
 */
function bailFsModuleCacheForGlobModules(): Plugin {
  return {
    name: 'atlas:fs-module-cache-glob-bail',
    configureVitest({ defineCacheKeyGenerator }) {
      defineCacheKeyGenerator(({ sourceCode }) => (sourceCode.includes('import.meta.glob') ? false : undefined))
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), bailFsModuleCacheForGlobModules()],
  server: {
    host: '127.0.0.1',
    port: 3000,
    strictPort: true,
  },
  test: {
    // ATL-258: measured on a 10-core Mac (4 performance cores), a full run takes the slowest
    // page tests to 1.3-3.7 s, and `--maxWorkers=100%` beside a second run takes the same tests
    // to 4-6 s (4-8x) with nothing wrong in them: whole Settings pages and the onboarding walk in
    // jsdom, and stories in a Chromium sharing the cores (4.0-4.7 s each). The 5 s default
    // failed them at random. Real faults found under load were fixed in the tests themselves.
    testTimeout: 15_000,
    projects: [
      {
        extends: true,
        test: {
          name: 'unit',
          environment: 'jsdom',
          globals: true,
          setupFiles: ['./src/test/setup.ts'],
          include: ['src/**/*.test.{ts,tsx}'],
        },
      },
      {
        // Every story runs as a test in real Chromium; play functions assert computed styles.
        extends: true,
        plugins: [storybookTest({ configDir: path.join(dirname, '.storybook') })],
        // ATL-258: the browser project's server inherits the dev server's strictPort (port 3000
        // must stay fixed for Sanctum). With it, a second `vitest --run` at the same time dies on
        // "Port 63315 is already in use"; without it, Vitest moves to the next free port.
        server: { strictPort: false },
        test: {
          name: 'storybook',
          browser: {
            enabled: true,
            headless: true,
            provider: playwright({}),
            instances: [{ browser: 'chromium' }],
          },
        },
      },
      {
        // The e2e support helpers (ATL-187) are plain node modules with no DOM; no jsdom/setup needed.
        extends: true,
        test: {
          name: 'e2e-support',
          environment: 'node',
          include: ['e2e/support/**/*.test.ts'],
        },
      },
    ],
  },
})
