/**
 * The real stack the recorded flows (e2e/flows/) run against (ATL-25): Laravel on its own port and
 * its own database, trading_core in OKX fixture mode, and a second Vite server pointed at that
 * Laravel. Nothing here touches the owner's `atlas` database, ports 3000/8000/8100 or Redis DB 0.
 *
 * Every value is derived from the checkout path, so two worktrees run side by side and the config,
 * the setup project and every worker agree without sharing state.
 */
import { createHash } from 'node:crypto'
import { realpathSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
export const REPO_ROOT = realpathSync(path.resolve(FRONTEND_DIR, '..'))
export const BACKEND_DIR = path.join(REPO_ROOT, 'backend')
export const SERVICES_DIR = path.join(REPO_ROOT, 'services')
/** Screenshots and run output live beside the repository, never inside it. */
export const EVIDENCE_DIR = path.resolve(REPO_ROOT, '..', 'evidence')

const digest = (label: string) => createHash('sha256').update(`${label}:${REPO_ROOT}`).digest()
const num = (name: string, fallback: number) => Number(process.env[name] ?? fallback)

export const PORTS = {
  /** The mocked specs' Vite (their page.route fakes answer http://localhost:8000). */
  mocked: num('E2E_PORT', 3140),
  /** The flows' Vite, talking to the real Laravel below. */
  spa: num('E2E_FLOW_PORT', 3141),
  api: num('E2E_API_PORT', 8140),
  core: num('E2E_CORE_PORT', 8141),
  /** Reverb for the live War Room flow (ATL-40); the spec starts and stops it itself. */
  reverb: num('E2E_REVERB_PORT', 8142),
}

export const SPA_URL = `http://127.0.0.1:${PORTS.spa}`
export const API_URL = `http://127.0.0.1:${PORTS.api}`
export const CORE_URL = `http://127.0.0.1:${PORTS.core}`

/** `atlas_e2e_<8 hex>`; E2eSeeder and tests/E2e/database.php refuse any other prefix. */
export const DATABASE =
  process.env.DB_E2E_DATABASE || `atlas_e2e_${createHash('sha1').update(REPO_ROOT).digest('hex').slice(0, 8)}`

/** Test-only values for a throwaway database; never the owner's application key or shared secret. */
const E2E_APP_KEY = `base64:${digest('atlas-e2e-app-key').toString('base64')}`
const E2E_INTERNAL_SECRET = digest('atlas-e2e-internal-secret').toString('hex')
const E2E_REVERB_SECRET = digest('atlas-e2e-reverb-secret').toString('hex')

/** The e2e Reverb app: Laravel signs channel auth with it, the SPA's Echo connects with its key. */
export const REVERB = { id: 'atlas-e2e', key: 'atlas-e2e-key', host: '127.0.0.1', port: PORTS.reverb, scheme: 'http' }

const db = {
  DB_CONNECTION: 'pgsql',
  DB_HOST: process.env.DB_HOST ?? '127.0.0.1',
  DB_PORT: process.env.DB_PORT ?? '5433',
  DB_USERNAME: process.env.DB_USERNAME ?? 'atlas',
  DB_PASSWORD: process.env.DB_PASSWORD ?? 'atlas',
  DB_DATABASE: DATABASE,
}

export const BACKEND_ENV: Record<string, string> = {
  ...db,
  APP_NAME: 'Atlas',
  APP_ENV: 'local',
  APP_DEBUG: 'true',
  APP_KEY: E2E_APP_KEY,
  APP_URL: API_URL,
  FRONTEND_URL: SPA_URL,
  SESSION_DRIVER: 'database',
  SESSION_DOMAIN: 'null',
  CACHE_STORE: 'database',
  QUEUE_CONNECTION: 'sync',
  // Channel auth (/broadcasting/auth) signs with the Reverb app; nothing in the API broadcasts.
  BROADCAST_CONNECTION: 'reverb',
  REVERB_APP_ID: REVERB.id,
  REVERB_APP_KEY: REVERB.key,
  REVERB_APP_SECRET: E2E_REVERB_SECRET,
  REVERB_HOST: REVERB.host,
  REVERB_PORT: String(REVERB.port),
  REVERB_SCHEME: REVERB.scheme,
  REVERB_SERVER_HOST: REVERB.host,
  REVERB_SERVER_PORT: String(REVERB.port),
  LOG_CHANNEL: 'single',
  LOG_LEVEL: 'warning',
  REDIS_CLIENT: 'predis',
  REDIS_HOST: process.env.REDIS_HOST ?? '127.0.0.1',
  REDIS_PORT: process.env.REDIS_PORT ?? '6380',
  // Settings changes publish to a Redis stream; keep e2e events out of the owner's DB 0.
  REDIS_DB: process.env.E2E_REDIS_DB ?? '13',
  BCRYPT_ROUNDS: '4',
  TRADING_CORE_URL: CORE_URL,
  INTERNAL_SHARED_SECRET: E2E_INTERNAL_SECRET,
  ATLAS_DEFAULT_ENV: 'demo',
  PHP_CLI_SERVER_WORKERS: '4',
}

export const CORE_ENV: Record<string, string> = {
  ...db,
  APP_KEY: E2E_APP_KEY,
  INTERNAL_SHARED_SECRET: E2E_INTERNAL_SECRET,
  ATLAS_ENV: 'demo',
  TRADING_CORE_HOST: '127.0.0.1',
  TRADING_CORE_PORT: String(PORTS.core),
  // Recorded OKX JSON instead of OKX, and no background sync over the seeded Portfolio data.
  ATLAS_OKX_FIXTURES: '1',
  TRADING_CORE_SCHEDULER: '0',
}

/** The flows' Vite: the real Laravel and the e2e Reverb. */
export const SPA_VARS: Record<string, string> = {
  VITE_API_URL: API_URL,
  VITE_REVERB_APP_KEY: REVERB.key,
  VITE_REVERB_HOST: REVERB.host,
  VITE_REVERB_PORT: String(REVERB.port),
  VITE_REVERB_SCHEME: REVERB.scheme,
}

/** process.env plus overrides, typed for child_process and Playwright's webServer.env. */
export function withEnv(extra: Record<string, string>): Record<string, string> {
  const base: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env)) if (v !== undefined) base[k] = v
  return { ...base, ...extra }
}

/** The owner E2eSeeder creates (backend/database/seeders/E2eSeeder.php); test-only. */
export const OWNER = { email: 'owner@atlas.e2e', password: 'e2e-owner-password-1' }
export const STORAGE_STATE = path.join(EVIDENCE_DIR, 'playwright', `auth-${DATABASE}.json`)
export const SHOTS = path.join(EVIDENCE_DIR, 'ATL-25')
