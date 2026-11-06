import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import {
  changesFor,
  crossesMidnight,
  DEFAULTS,
  diffFromTeam,
  draftFrom,
  inZone,
  parseTime,
  previewTimes,
  slots,
  steppedInterval,
  usWeekend,
} from './model'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const BALANCED = {
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
}
const PRESETS = { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED }

/** Strip the LTR isolates the page puts round every time. */
const plain = (s: string | null | undefined) => (s ?? '').replace(/[⁦-⁩]/g, '')

const scheduleRow = (over: Record<string, unknown> = {}) =>
  Object.fromEntries(Object.entries({ ...DEFAULTS, ...over }).map(([k, v]) => [`schedule.${k}`, v]))

type Patch = { env: string; version: number; changes: Record<string, unknown> }

function fakeBackend(opts: { schedule?: Record<string, unknown>; zone?: string; reject?: (p: Patch) => Response | null } = {}) {
  const state = {
    version: 7,
    schedule: scheduleRow(opts.schedule),
    patches: [] as Patch[],
    history: [{ id: 'h1', version: 1, diff: { risk_profile: { from: null, to: 'balanced' } }, changed_by: null, created_at: '2026-11-01T08:00:00Z' }] as {
      id: string
      version: number
      diff: Record<string, { from: unknown; to: unknown }>
      changed_by: null
      created_at: string
    }[],
  }
  const doc = () => ({
    data: {
      id: 's1',
      env: 'demo',
      version: state.version,
      settings: { risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, onboarding_completed: 1, ...state.schedule },
      presets: PRESETS,
      max_enabled_autonomy_level: 4,
      updated_at: '2026-11-06T09:00:00Z',
    },
  })
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
      if (path === '/api/me/preferences') return reply(200, { data: { name: 'Mohammed Ali', locale: 'en', timezone: opts.zone ?? 'Asia/Baghdad', theme: 'system' } })
      if (path === '/api/settings?env=demo' && method === 'GET') return reply(200, doc())
      if (path.startsWith('/api/settings/history')) return reply(200, { data: state.history, meta: { total: state.history.length } })
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as Patch
        const refused = opts.reject?.(body)
        if (refused) return refused
        state.patches.push(body)
        const diff = Object.fromEntries(Object.entries(body.changes).map(([k, v]) => [k, { from: state.schedule[k], to: v }]))
        state.schedule = { ...state.schedule, ...(body.changes as Record<string, string | number | boolean>) }
        state.version += 1
        state.history = [{ id: `h${state.version}`, version: state.version, diff, changed_by: null, created_at: '2026-11-06T09:30:00Z' }, ...state.history]
        return reply(200, doc())
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

const json422 = (key: string, msg: string) =>
  new Response(JSON.stringify({ message: msg, errors: { [`changes.${key}`]: [msg] } }), { status: 422, headers: { 'Content-Type': 'application/json' } })

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/schedule'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const timeBox = (name: string) => screen.getByRole('textbox', { name: new RegExp(`^${name}(, changed)?$`) })
const EVERY = /^Every(, changed)?$/

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('schedule maths', () => {
  it('slots run first + k × interval up to and including the last slot', () => {
    expect(previewTimes(slots('08:10', '23:00', 30))).toBe('08:10, 08:40 … 22:40')
    expect(previewTimes(slots('08:10', '23:00', 60))).toBe('08:10, 09:10 … 22:10')
    expect(slots('08:10', '23:00', 60)).toHaveLength(15)
    expect(previewTimes(slots('08:00', '09:00', 30))).toBe('08:00, 08:30, 09:00')
    expect(slots('22:00', '08:00', 30)).toEqual([])
    expect(slots('08:10', '23:00', 5)).toEqual([])
  })

  it('reads typed times, steps the interval on its 15-minute grid, and knows when quiet hours cross midnight', () => {
    expect(parseTime('7:30')).toBe('07:30')
    expect(parseTime('0730')).toBe('07:30')
    expect(parseTime('٠٧:٣٠')).toBe('07:30')
    expect(parseTime('25:00')).toBeNull()
    expect(steppedInterval(30, 1)).toBe(45)
    expect(steppedInterval(240, 1)).toBe(240)
    expect(steppedInterval(15, -1)).toBe(15)
    expect(crossesMidnight('23:00', '07:00')).toBe(true)
    expect(crossesMidnight('01:00', '07:00')).toBe(false)
  })

  it('sends only the changed schedule.* keys, the interval as an integer', () => {
    const d = draftFrom(DEFAULTS)
    expect(changesFor({ ...d, interval: '60' }, DEFAULTS)).toEqual({ 'schedule.analysis_interval_minutes': 60 })
    expect(changesFor({ ...d, values: { ...d.values, quiet_hours_enabled: true } }, DEFAULTS)).toEqual({ 'schedule.quiet_hours_enabled': true })
  })

  it('converts a Baghdad time to the owner zone, with the day it falls on', () => {
    const at = Date.UTC(2026, 10, 6, 12)
    expect(diffFromTeam('Europe/London', at)).toBe(-180)
    expect(diffFromTeam('Asia/Baghdad', at)).toBe(0)
    expect(inZone('07:30', -180)).toEqual({ time: '04:30', shift: 0 })
    expect(inZone('01:00', -180)).toEqual({ time: '22:00', shift: -1 })
    expect(inZone('23:30', 120)).toEqual({ time: '01:30', shift: 1 })
  })

  it('the US weekend is Friday 20:00 to Sunday 20:00 New York, 48 hours apart', () => {
    const { from, to } = usWeekend(Date.UTC(2026, 10, 4, 12))
    expect(to - from).toBe(48 * 3600e3)
    // 6 Nov 2026 is after the US clock change: 20:00 EST = 01:00 UTC Saturday.
    expect(new Date(from).toISOString()).toBe('2026-11-07T01:00:00.000Z')
  })
})

describe('Settings → Schedule page (ATL-230)', () => {
  it('shows every saved schedule value', async () => {
    fakeBackend({ schedule: { pm_daily_report: '23:45', historian_weekly_review: 'Sat 17:00', stock_perps_weekend: 'no_new_trades' } })
    renderPage()
    expect(await screen.findByRole('heading', { level: 2, name: 'Schedule' })).toBeInTheDocument()
    await waitFor(() => expect(timeBox('Morning brief')).toHaveValue('07:30'))
    expect(timeBox('Regime table')).toHaveValue('07:30')
    expect(timeBox('Watchlist')).toHaveValue('08:00')
    expect(timeBox('Daily report')).toHaveValue('23:45')
    expect(timeBox('Weekly review')).toHaveValue('17:00')
    expect(screen.getByRole('combobox', { name: 'Weekly review day' })).toHaveValue('Sat')
    expect(screen.getByRole('textbox', { name: EVERY })).toHaveValue('30')
    expect(timeBox('First cycle')).toHaveValue('08:10')
    expect(timeBox('Last cycle')).toHaveValue('23:00')
    expect(screen.getByRole('switch', { name: 'Quiet hours' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('radio', { name: /No new trades/ })).toHaveAttribute('aria-checked', 'true')
    expect(plain(screen.getByTestId('sch-cap').textContent)).toBe('30 analysis cycles a day, every 30 minutes from 08:10 to 23:00.')
    // Baghdad owner: no second clock.
    expect(screen.queryByTestId('sch-zone')).toBeNull()
  })

  it('interval 60 updates the preview before saving, then saves one versioned PATCH and lists it in Activity', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(plain(screen.getByTestId('sch-preview').textContent)).toBe('Cycles at 08:10, 08:40 … 22:40'))

    const every = screen.getByRole('textbox', { name: EVERY })
    await user.clear(every)
    await user.type(every, '60')
    expect(plain(screen.getByTestId('sch-preview').textContent)).toBe('Cycles at 08:10, 09:10 … 22:10')
    expect(plain(screen.getByTestId('sch-cap').textContent)).toBe('15 analysis cycles a day, every hour from 08:10 to 23:00.')
    expect(state.patches).toEqual([])

    await user.click(screen.getByTestId('sch-save'))
    await waitFor(() => expect(state.patches).toHaveLength(1))
    expect(state.patches[0]).toEqual({ env: 'demo', version: 7, changes: { 'schedule.analysis_interval_minutes': 60 } })
    await waitFor(() => expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 8'))
    expect(screen.getByRole('textbox', { name: EVERY })).toHaveValue('60')
    const latest = within(await screen.findByTestId('sch-activity')).getAllByRole('listitem')[0]!
    await waitFor(() => expect(plain(latest.textContent)).toContain('Analysis interval: 30 min → 60 min'))
  })

  it('the stepper moves the interval by 15 minutes', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(screen.getByRole('textbox', { name: EVERY })).toHaveValue('30'))
    await user.click(screen.getByRole('button', { name: 'More cycles' }))
    expect(screen.getByRole('textbox', { name: EVERY })).toHaveValue('45')
    expect(plain(screen.getByTestId('sch-preview').textContent)).toBe('Cycles at 08:10, 08:55 … 22:25')
  })

  it("first slot after the last: the server's 422 shows under the first slot and nothing is saved", async () => {
    const msg = 'The First analysis cycle must be earlier than the Last analysis cycle.'
    const state = fakeBackend({ reject: (p) => ('schedule.analysis_first_slot' in p.changes ? json422('schedule.analysis_first_slot', msg) : null) })
    const user = userEvent.setup()
    renderPage()
    const first = await screen.findByRole('textbox', { name: 'First cycle' })
    await waitFor(() => expect(first).toHaveValue('08:10'))
    await user.clear(first)
    await user.type(first, '23:30')
    // The page leaves the order rule to the server: Save is on.
    expect(screen.queryByTestId('sch-analysis_first_slot-err')).toBeNull()
    expect(screen.getByTestId('sch-save')).toBeEnabled()
    await user.click(screen.getByTestId('sch-save'))

    const err = await screen.findByTestId('sch-analysis_first_slot-err')
    expect(err).toHaveTextContent(msg)
    expect(screen.getByRole('textbox', { name: 'First cycle' })).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('textbox', { name: 'First cycle' })).toHaveAccessibleDescription(msg)
    // Nothing reached the store; the version is unchanged and the owner's text is kept.
    expect(state.patches).toEqual([])
    expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 7')
    expect(screen.getByRole('textbox', { name: 'First cycle' })).toHaveValue('23:30')
    expect(screen.getByTestId('sch-save')).toBeDisabled()

    // Fixing either slot clears the message.
    await user.clear(screen.getByRole('textbox', { name: 'First cycle' }))
    await user.type(screen.getByRole('textbox', { name: 'First cycle' }), '09:00')
    expect(screen.queryByTestId('sch-analysis_first_slot-err')).toBeNull()
  })

  it('a time that is not a time shows the format error when the field is left', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const brief = await screen.findByRole('textbox', { name: 'Morning brief' })
    await waitFor(() => expect(brief).toHaveValue('07:30'))
    await user.clear(brief)
    await user.type(brief, '25:00')
    await user.tab()
    expect(await screen.findByTestId('sch-news_morning_brief-err')).toHaveTextContent('Use 24-hour time, like 07:30.')
    expect(screen.getByTestId('sch-save')).toBeDisabled()
  })

  it('quiet hours 23:00–07:00 read as ending the next day', async () => {
    fakeBackend({ schedule: { quiet_hours_enabled: true, quiet_hours_start: '23:00', quiet_hours_end: '07:00' } })
    renderPage()
    await waitFor(() => expect(timeBox('To')).toHaveValue('07:00'))
    expect(timeBox('From')).toHaveValue('23:00')
    expect(plain(screen.getByTestId('sch-quiet_hours_end-sub').textContent)).toBe('Until 07:00 the next day.')
    // The 23:30 daily report falls inside; the last cycle (22:40) does not.
    const notes = plain(screen.getByTestId('sch-qnotes').textContent)
    expect(notes).toContain('The daily report at 23:30 falls in quiet hours.')
    expect(notes).not.toContain('analysis cycles')
    // Moving the start to 22:00 catches the 22:10 and 22:40 cycles.
    const user = userEvent.setup()
    await user.clear(timeBox('From'))
    await user.type(timeBox('From'), '22:00')
    expect(plain(screen.getByTestId('sch-qnotes').textContent)).toContain('2 of 30 analysis cycles fall in quiet hours')
  })

  it('a stale version (409) reloads the saved values and offers to apply the edits again', async () => {
    const state = fakeBackend()
    let first = true
    state.version = 7
    vi.mocked(fetch).mockImplementation(
      ((orig) => async (input: RequestInfo | URL, init: RequestInit = {}) => {
        if (String(input).endsWith('/api/settings') && (init.method ?? 'GET').toUpperCase() === 'PATCH' && first) {
          first = false
          state.version = 8
          state.schedule = { ...state.schedule, 'schedule.pm_daily_report': '23:45' }
          return new Response(JSON.stringify({ message: 'stale', current_version: 8 }), { status: 409, headers: { 'Content-Type': 'application/json' } })
        }
        return orig(input, init)
      })(vi.mocked(fetch).getMockImplementation()!),
    )
    const user = userEvent.setup()
    renderPage()
    const every = await screen.findByRole('textbox', { name: EVERY })
    await waitFor(() => expect(every).toHaveValue('30'))
    await user.clear(every)
    await user.type(every, '60')
    await user.click(screen.getByTestId('sch-save'))

    const notice = await screen.findByTestId('sch-conflict')
    expect(notice).toHaveTextContent('The schedule changed in another window')
    expect(plain(notice.textContent)).toContain('Analysis interval: 30 min → 60 min')
    // The page now shows version 8, with the other window's daily report.
    await waitFor(() => expect(timeBox('Daily report')).toHaveValue('23:45'))
    expect(screen.getByRole('textbox', { name: EVERY })).toHaveValue('30')

    await user.click(within(notice).getByRole('button', { name: 'Apply my changes again' }))
    expect(screen.getByRole('textbox', { name: EVERY })).toHaveValue('60')
    await user.click(screen.getByTestId('sch-save'))
    await waitFor(() => expect(state.patches.at(-1)).toEqual({ env: 'demo', version: 8, changes: { 'schedule.analysis_interval_minutes': 60 } }))
  })

  it('an owner in London sees the London moment beside each Baghdad time', async () => {
    fakeBackend({ zone: 'Europe/London' })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('sch-zone')).toBeInTheDocument())
    expect(plain(screen.getByTestId('sch-zone').textContent)).toBe(
      'Your time zone is London, 3 hours behind Baghdad. Beside each time is the same moment in London.',
    )
    expect(plain(screen.getByTestId('sch-news_morning_brief-alt').textContent)).toBe('04:30 in London')
    expect(plain(screen.getByTestId('sch-historian_weekly_review-alt').textContent)).toBe('Sun 15:00 in London')
  })

  it('Arabic: right-to-left labels, the times stay in Western digits', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    renderPage()
    expect(await screen.findByRole('heading', { level: 2, name: 'الجدول' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'الموجز الصباحي' })).toHaveValue('07:30'))
    expect(plain(screen.getByTestId('sch-preview').textContent)).toBe('الدورات الساعة 08:10, 08:40 … 22:40')
    expect(screen.getByRole('textbox', { name: 'الموجز الصباحي' })).toHaveAttribute('dir', 'ltr')
  })
})
