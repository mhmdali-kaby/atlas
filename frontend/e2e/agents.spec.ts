import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-117: the Agents page against a fake Laravel serving ATL-113's GET /api/agents and
 * GET /api/agents/claude-status (docs/handoff/ATL-113-agents-api.md). Screenshots go to
 * ../../evidence/ATL-117/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-117'

const NOW = Date.now()
const ago = (min: number) => new Date(NOW - min * 60_000).toISOString()

type Agent = Record<string, unknown>

function agent(slug: string, name: string, over: Agent = {}): Agent {
  return {
    slug,
    name,
    handle: name,
    enabled: true,
    model: 'strong',
    model_name: 'opus',
    state: 'idle',
    state_since: ago(20),
    current_task: null,
    today: { tokens: 412300, tokens_in: 400000, tokens_out: 12300, turns: 14, runs: 6, cost_usd_notional: '0.380000' },
    budget: { daily_tokens: 3000000, daily_turns: 300 },
    prompt: { sequence: 2, sha: '4f1e9a2c7b30', sha256: '4f1e9a2c7b30'.padEnd(64, '0'), created_at: ago(60 * 30) },
    last_run: { id: `run-${slug}`, status: 'ok', trigger: 'mention', started_at: ago(12), ended_at: ago(11), prompt_version: 2 },
    ...over,
  }
}

const AGENTS = [
  agent('orchestrator', 'Orchestrator', { model: 'standard', model_name: 'sonnet' }),
  agent('market_scout', 'Market Scout', { current_task: 'Next watchlist tomorrow at 08:00', last_run: { status: 'ok', trigger: 'schedule', started_at: ago(200), ended_at: ago(198) } }),
  // 85% of its token budget: the warning state.
  agent('news_intelligence', 'News Intelligence', {
    state: 'thinking',
    state_since: ago(1),
    current_task: 'Reading 14 new stories',
    today: { tokens: 1275000, tokens_in: 1200000, tokens_out: 75000, turns: 61, runs: 9, cost_usd_notional: '1.240000' },
    budget: { daily_tokens: 1500000, daily_turns: 300 },
    last_run: { status: 'running', trigger: 'alert', started_at: ago(1), ended_at: null },
  }),
  agent('historian', 'Historian', { state: 'rate_limited', state_since: ago(20), current_task: 'Waiting for your Claude plan’s usage limit to reset', last_run: { status: 'rate_limited', trigger: 'schedule', started_at: ago(21), ended_at: ago(20) } }),
  agent('technical_analyst', 'Technical Analyst', { enabled: false, state: 'disabled', state_since: null, today: { tokens: 0, tokens_in: 0, tokens_out: 0, turns: 0, runs: 0, cost_usd_notional: '0.000000' }, prompt: null, last_run: null }),
  agent('derivatives_sentiment', 'Derivatives & Sentiment', { model: 'standard', model_name: 'sonnet', state: 'waiting', current_task: 'Next check at 19:00' }),
  agent('strategist', 'Strategist', { state: 'paused', current_task: 'Paused by you at 13:10' }),
  agent('devils_advocate', 'Devil’s Advocate', { state: 'degraded', last_run: { status: 'degraded', trigger: 'mention', started_at: ago(50), ended_at: ago(49) } }),
  agent('portfolio_manager', 'Portfolio Manager', { model: 'routine', model_name: 'haiku', last_run: { status: 'error', trigger: 'schedule', started_at: ago(5), ended_at: ago(5) } }),
]

const REPORT = {
  data: AGENTS,
  meta: { env: 'demo', day: '2026-10-30', timezone: 'Asia/Baghdad', day_start: ago(600), day_end: ago(-840), settings_version: 4 },
}

function claude(over: Record<string, unknown> = {}) {
  const loggedIn = over.logged_in ?? true
  return {
    ok: loggedIn,
    installed: true,
    logged_in: loggedIn,
    api_key_env_clear: true,
    login_command: 'claude login',
    checks: [
      { name: 'claude_cli', ok: true, status: 'pass', detail: 'Installed (2.4.1 (Claude Code))' },
      loggedIn
        ? { name: 'claude_cli_login', ok: true, status: 'pass', detail: 'Logged in' }
        : { name: 'claude_cli_login', ok: false, status: 'fail', detail: 'Not logged in. Run `claude login` in a terminal on this Mac, then check again.' },
      { name: 'claude_api_key', ok: true, status: 'pass', detail: 'ANTHROPIC_API_KEY is not set' },
    ],
    checked_at: new Date(NOW).toISOString(),
    ...over,
  }
}

async function fakeApi(page: Page, opts: { failAgents?: boolean; loggedIn?: boolean } = {}) {
  const calls = { agents: 0, claude: 0, failAgents: opts.failAgents ?? false }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/agents') {
      calls.agents += 1
      if (calls.failAgents) return json(500, { message: 'Server Error' })
      return json(200, REPORT)
    }
    if (url.pathname === '/api/agents/claude-status') {
      calls.claude += 1
      return json(200, { data: claude({ logged_in: opts.loggedIn ?? true }) })
    }
    if (url.pathname === '/broadcasting/auth') return json(403, { message: 'Forbidden' })
    return json(404, { message: 'Not found' })
  })
  return calls
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.goto('/agents')
}

test('lists the nine agents with state words, usage meters and prompt versions; 85% usage warns', async ({ page }) => {
  await fakeApi(page)
  await open(page, 'en', 'light')
  const rows = page.getByTestId('ag-row')
  await expect(rows).toHaveCount(9)
  await expect(page.locator('nav[aria-label="Areas"] a[aria-current="true"]')).toHaveText('Agents')

  const news = page.locator('[data-testid="ag-row"][data-slug="news_intelligence"]')
  await expect(news.getByTestId('ag-state')).toContainText('Thinking')
  const tokens = news.getByTestId('ag-meter-tokens')
  await expect(tokens).toHaveAttribute('data-warn', 'true')
  await expect(tokens).toHaveClass(/is-warn/)
  await expect(tokens).toContainText('85%')
  await expect(tokens).toContainText('1.27M of 1.5M')
  await expect(tokens).toHaveAttribute('aria-label', 'Tokens: 1.27M of 1.5M used today, 85%.')
  await expect(tokens.locator('.ag-bar i')).toHaveCSS('background-color', /.+/)
  // The other meter on that row is under 80%: no warning.
  await expect(news.getByTestId('ag-meter-turns')).toHaveAttribute('data-warn', 'false')

  // State as text for every state the API sends.
  const state = (slug: string) => page.locator(`[data-testid="ag-row"][data-slug="${slug}"]`).getByTestId('ag-state')
  await expect(state('orchestrator')).toContainText('Idle')
  await expect(state('historian')).toContainText('Usage limit')
  await expect(state('technical_analyst')).toContainText('Off')
  await expect(state('derivatives_sentiment')).toContainText('Waiting')
  await expect(state('strategist')).toContainText('Paused')
  await expect(state('devils_advocate')).toContainText('Degraded')

  // Prompt versions: "v2" and the 12-character code; none for an agent with no prompt.
  await expect(page.getByTestId('ag-prompt')).toHaveCount(8)
  await expect(page.getByTestId('ag-prompt').first()).toContainText('v2')
  await expect(page.getByTestId('ag-prompt').first()).toContainText('4f1e9a2c7b30')
  await expect(page.locator('[data-testid="ag-row"][data-slug="technical_analyst"]')).toContainText('No prompt saved yet')
  await expect(page.locator('[data-testid="ag-row"][data-slug="technical_analyst"]')).toContainText('Turn on in Settings → Agents')

  // Notional cost labelled as such; last run with its trigger.
  await expect(news.getByTestId('ag-cost')).toHaveText('$1.24 at API prices')
  await expect(news.getByTestId('ag-run')).toContainText('Running')
  await expect(news).toContainText('after a news alert')

  // The sidebar lists the team; a row jumps to the agent on the page.
  const side = page.getByTestId('ag-sidebar')
  await expect(side.locator('.agent-row')).toHaveCount(9)
  await side.getByRole('button', { name: 'Historian, Usage limit' }).click()
  await expect(page.locator('#ag-row-historian')).toBeFocused()

  await page.screenshot({ path: `${SHOTS}/agents-ltr-light.png`, fullPage: true })
})

test('logged out: the Claude panel shows the claude login command', async ({ page }) => {
  await fakeApi(page, { loggedIn: false })
  await open(page, 'en', 'light')
  const panel = page.getByTestId('ag-claude')
  await expect(panel).toHaveAttribute('data-view', 'logged_out')
  await expect(panel.getByRole('heading')).toHaveText('Claude isn’t signed in on this Mac')
  await expect(panel.getByTestId('ag-login-command')).toHaveText('claude login')
  await expect(panel.getByRole('button', { name: 'Copy the command claude login' })).toBeVisible()
  await expect(panel).toContainText('Not signed in')
  await panel.screenshot({ path: `${SHOTS}/claude-logged-out.png` })
})

test('API error: the error state retries and refetches', async ({ page }) => {
  const calls = await fakeApi(page, { failAgents: true })
  await open(page, 'en', 'light')
  const error = page.getByTestId('ag-error')
  // TanStack Query retries a failed load three times (about 7 s) before the error shows.
  await expect(error).toBeVisible({ timeout: 15_000 })
  await expect(error).toHaveAttribute('role', 'alert')
  await expect(error.getByRole('heading')).toHaveText('Couldn’t load the agents')
  await page.screenshot({ path: `${SHOTS}/agents-error.png`, fullPage: true })
  const before = calls.agents
  calls.failAgents = false
  await error.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByTestId('ag-row')).toHaveCount(9)
  expect(calls.agents).toBeGreaterThan(before)
})

for (const [lang, theme] of [
  ['ar', 'light'],
  ['ar', 'dark'],
  ['en', 'dark'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  test(`renders ${dir}-${theme}: meters fill from the start side, nothing clipped`, async ({ page }) => {
    await fakeApi(page)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    await expect(page.getByTestId('ag-row')).toHaveCount(9)

    // Every non-empty bar starts at the inline start of its track.
    const bars = page.locator('.ag-bar')
    const n = await bars.count()
    expect(n).toBeGreaterThan(0)
    for (let i = 0; i < n; i += 1) {
      const track = await bars.nth(i).boundingBox()
      const fill = await bars.nth(i).locator('i').boundingBox()
      if (!track || !fill || fill.width === 0) continue
      if (dir === 'rtl') expect(Math.abs(track.x + track.width - (fill.x + fill.width))).toBeLessThan(1)
      else expect(Math.abs(track.x - fill.x)).toBeLessThan(1)
    }

    // No text is cut: no element in the page overflows its own box horizontally (the task line
    // clamps to two lines by design and is excluded; its full text is in its title).
    const clipped = await page.evaluate(() => {
      const out: string[] = []
      for (const el of document.querySelectorAll<HTMLElement>('.ag-page *')) {
        if (el.closest('.ag-task, .ag-bar, svg, .ag-prompt-k, .sr-only')) continue
        if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible') out.push(`${el.className} :: ${el.textContent?.slice(0, 40)}`)
      }
      return out
    })
    expect(clipped).toEqual([])

    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/agents-${dir}-${theme}.png`, fullPage: true })
  })
}
