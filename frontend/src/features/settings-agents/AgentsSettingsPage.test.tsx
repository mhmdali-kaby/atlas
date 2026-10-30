import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import ar from '../../i18n/locales/ar/settings-agents.json'
import en from '../../i18n/locales/en/settings-agents.json'
import arRaw from '../../i18n/locales/ar/settings-agents.json?raw'
import enRaw from '../../i18n/locales/en/settings-agents.json?raw'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { ROSTER } from './model'

/**
 * Every "key": string token (object property or object value) at brace-depth 1+, bucketed by
 * the object it sits in. JSON.parse silently keeps the last of a duplicate, so checking the
 * parsed object can never see one; this walks the raw text instead.
 */
function duplicateKeys(raw: string): string[] {
  const dups: string[] = []
  const stack: Set<string>[] = []
  let i = 0
  while (i < raw.length) {
    const ch = raw[i]
    if (ch === '"') {
      const start = i
      i++
      while (i < raw.length && raw[i] !== '"') i += raw[i] === '\\' ? 2 : 1
      i++
      const token = raw.slice(start, i)
      let j = i
      while (j < raw.length && /\s/.test(raw[j] ?? '')) j++
      const top = stack[stack.length - 1]
      if (raw[j] === ':' && top) {
        const key = JSON.parse(token) as string
        if (top.has(key)) dups.push(key)
        else top.add(key)
      }
      continue
    }
    if (ch === '{') {
      stack.push(new Set())
    } else if (ch === '}') {
      stack.pop()
    }
    i++
  }
  return dups
}

/*
 * ATL-157: webIntro and allowNote described web search as unaffected by the site lists, and an
 * empty allow list as "any public site". Since decision 248 (ATL-121) and ATL-123, both lists
 * also govern WebSearch, and an empty allow list blocks everything. This checks the fixed copy
 * renders in both languages, and that the stale phrases are gone from both locale files.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const STRONG = new Set(['market_scout', 'news_intelligence', 'historian', 'strategist', 'devils_advocate'])

function fakeBackend(opts: { allow?: string[] } = {}) {
  const settings: Record<string, unknown> = { risk_profile: 'balanced', autonomy_level: 2, onboarding_completed: 1 }
  for (const slug of ROSTER) {
    settings[`agents.${slug}.enabled`] = true
    settings[`agents.${slug}.model`] = STRONG.has(slug) ? 'strong' : 'standard'
    settings[`agents.${slug}.daily_tokens`] = 3_000_000
    settings[`agents.${slug}.daily_turns`] = 300
  }
  settings['agents.web.allow_domains'] = opts.allow ?? []
  settings['agents.web.deny_domains'] = ['x.com']
  const state = { version: 4, settings }
  const agents = ROSTER.map((slug) => ({ slug, today: { tokens: 0, turns: 0 } }))

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections') return reply(200, { data: [] })
      if (path === '/api/agents?env=demo') return reply(200, { data: agents })
      if (path.startsWith('/api/settings/history')) return reply(200, { data: [] })
      if (path === '/api/settings?env=demo' && method === 'GET')
        return reply(200, {
          data: { id: 's1', env: 'demo', version: state.version, settings: state.settings, presets: {}, updated_at: '2026-10-28T09:00:00Z' },
        })
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/agents'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeEach(async () => {
  resetCsrfForTests()
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('Settings → Agents web access copy (ATL-157)', () => {
  it('says the lists also limit web search, in English', async () => {
    fakeBackend()
    renderPage()
    const web = await screen.findByTestId('web-access')
    expect(within(web).getByText(/web search/i, { selector: '.sa-row-sub' })).toBeInTheDocument()
    expect(web.textContent).not.toMatch(/isn.t limited/i)
  })

  it('says the lists also limit web search, in Arabic', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    renderPage()
    const web = await screen.findByTestId('web-access')
    expect(web.textContent ?? '').toContain('البحث')
    expect(web.textContent ?? '').not.toContain('أي موقع عام')
  })

  it('shows the "nothing allowed" note when the allow list is empty', async () => {
    fakeBackend({ allow: [] })
    renderPage()
    const note = await screen.findByTestId('allow-empty-note')
    expect(note).toHaveTextContent(en.allowEmpty)
  })

  it('shows the Arabic "nothing allowed" note when the allow list is empty', async () => {
    fakeBackend({ allow: [] })
    await i18n.changeLanguage('ar')
    renderPage()
    const note = await screen.findByTestId('allow-empty-note')
    expect(note).toHaveTextContent(ar.allowEmpty)
  })

  it('hides the note once a site is allowed', async () => {
    fakeBackend({ allow: ['coindesk.com'] })
    renderPage()
    await screen.findByTestId('web-access')
    expect(screen.queryByTestId('allow-empty-note')).toBeNull()
  })

  it('has no stale phrase and no duplicate keys in either locale file', () => {
    expect(enRaw).not.toMatch(/isn.t limited/i)
    expect(arRaw).not.toContain('أي موقع عام')
    expect(en.allowEmpty).toBeTruthy()
    expect(ar.allowEmpty).toBeTruthy()
    expect(duplicateKeys(enRaw)).toEqual([])
    expect(duplicateKeys(arRaw)).toEqual([])
  })
})
