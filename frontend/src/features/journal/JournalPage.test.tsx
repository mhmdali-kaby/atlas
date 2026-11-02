import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { resetCsrfForTests } from '../../lib/api'
import { apiParams, BLANK, readFilters, type JournalEntry } from './api'
import { LOSS, WIN } from './fixtures'
import { closeLabel, displayTags, miniGeometry, outcomeOf, tp1Hit, voteRight, weekEnd } from './model'
import journal from './routes'

/* ATL-202 against ATL-200's GET /api/journal and /api/journal/{id} (docs/handoff/ATL-200-journal-scores-api.md). */
const NOW = Date.parse('2026-10-28T11:40:00Z') // Wed 14:40 Baghdad

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function fakeBackend(opts: { entries?: JournalEntry[]; fail?: boolean } = {}) {
  const all = opts.entries ?? [WIN, LOSS]
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/journal') {
      if (opts.fail) return json(500, { message: 'Server Error' })
      const tag = url.searchParams.get('tag')
      const data = tag ? all.filter((e) => e.tags.includes(tag)) : all
      return json(200, { data, meta: { env: 'demo', per_page: 12, next_cursor: null, prev_cursor: null } })
    }
    const one = /^\/api\/journal\/(.+)$/.exec(url.pathname)
    if (one) {
      const e = all.find((x) => x.id === one[1])
      return e ? json(200, { data: e }) : json(404, { message: 'Not found' })
    }
    return json(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const journalCalls = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.map(([input]) => new URL(String(input))).filter((u) => u.pathname === '/api/journal')

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.pathname + loc.search}</output>
}

function renderJournal(url = '/journal') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: (
          <>
            <Where />
          </>
        ),
        children: [],
      },
      { path: '/journal', element: <>{journal.routes[0]!.element}<Where /></> },
      { path: '/warroom', element: <Where /> },
      { path: '/portfolio/positions', element: <Where /> },
    ],
    { initialEntries: [url] },
  )
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router, unmount: view.unmount }
}

const where = () => screen.getByTestId('where').textContent ?? ''
const rowOf = (inst: string) => screen.getAllByTestId('journal-row').find((r) => r.textContent?.includes(inst.split('-')[0]!))!

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Journal list', () => {
  it('registers /journal in its own Journal area with one Post-mortems item', () => {
    expect(journal.area).toBe('journal')
    expect(journal.routes.map((r) => r.path)).toEqual(['journal'])
    const nav = Array.isArray(journal.nav) ? journal.nav[0]! : journal.nav!
    expect(nav.path).toBe('/journal')
    expect(i18n.t(nav.labelKey)).toBe('Post-mortems')
  })

  it('shows each entry with its Baghdad date, outcome icon and signed R and USD', async () => {
    fakeBackend()
    renderJournal()
    const win = await waitFor(() => rowOf('BTC'))
    // closed_at 11:30 UTC is 14:30 in Baghdad
    expect(within(win).getByTestId('row-when')).toHaveTextContent('Tue 27 Oct, 14:30')
    expect(win).toHaveClass('is-win')
    expect(win.querySelector('.jr-row-out svg')).not.toBeNull()
    expect(within(win).getByText('+1.69R')).toBeInTheDocument()
    expect(win.querySelector('[data-sign="profit"] .jr-dir')).not.toBeNull()
    expect(within(win).getByTestId('row-usd')).toHaveTextContent('+$3.71')
    expect(win).toHaveTextContent('Closed at TP2')
    expect(win).toHaveTextContent('Pullback to support')
    expect(win).toHaveTextContent(/^Won.*BTC-USDT-SWAP/)

    const loss = rowOf('XAU')
    expect(within(loss).getByTestId('row-when')).toHaveTextContent('Tue 20 Oct, 18:10')
    expect(loss).toHaveClass('is-loss')
    expect(within(loss).getByText('−1.02R')).toBeInTheDocument()
    expect(loss.querySelector('[data-sign="loss"] .jr-dir')).not.toBeNull()
    expect(within(loss).getByTestId('row-usd')).toHaveTextContent('−$512.40')
    expect(loss).toHaveTextContent('Stopped out')
    // a pending lesson says so
    expect(loss).toHaveTextContent('Lesson still being written')
    // grouped by review week; the current one says when it is reviewed
    expect(screen.getByRole('region', { name: 'This week' })).toContainElement(win)
    expect(screen.getByRole('region', { name: 'Week to 25 Oct' })).toContainElement(loss)
    expect(screen.getByText(/That’s every post-mortem since the first trade on 20 Oct 2026/)).toBeInTheDocument()
  })

  it('choosing a tag puts it in the URL and asks /api/journal for it; a reload restores it', async () => {
    const fetch = fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const first = renderJournal()
    await waitFor(() => expect(screen.getAllByTestId('journal-row')).toHaveLength(2))
    expect(journalCalls(fetch)[0]!.searchParams.get('tag')).toBeNull()

    await user.click(screen.getByTestId('filter-tag'))
    await user.click(await screen.findByRole('menuitemradio', { name: 'Breakout' }))

    await waitFor(() => expect(where()).toBe('/journal?tag=setup%3Abreakout'))
    await waitFor(() => expect(journalCalls(fetch).at(-1)!.searchParams.get('tag')).toBe('setup:breakout'))
    await waitFor(() => expect(screen.getAllByTestId('journal-row')).toHaveLength(1))
    expect(screen.getByTestId('journal-chips')).toHaveTextContent('Breakout')
    expect(screen.getByTestId('filter-tag')).toHaveClass('is-set')
    first.unmount()

    // a reload of the same URL: the filter is back and the first request carries it
    fetch.mockClear()
    renderJournal('/journal?tag=setup%3Abreakout')
    await waitFor(() => expect(screen.getAllByTestId('journal-row')).toHaveLength(1))
    expect(journalCalls(fetch)[0]!.searchParams.get('tag')).toBe('setup:breakout')
    expect(screen.getByTestId('filter-tag')).toHaveTextContent('Breakout')
    expect(screen.getByTestId('journal-chips')).toHaveTextContent('Breakout')
  })

  it('removing the chip clears the filter from the URL', async () => {
    fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderJournal('/journal?outcome=loss&inst=XAU-USDT-SWAP')
    await screen.findByTestId('journal-chips')
    await user.click(screen.getByRole('button', { name: 'Remove filter: Lost' }))
    await waitFor(() => expect(where()).toBe('/journal?inst=XAU-USDT-SWAP'))
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(where()).toBe('/journal'))
  })

  it('shows the empty state when there are no entries yet', async () => {
    fakeBackend({ entries: [] })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderJournal()
    expect(await screen.findByTestId('journal-empty')).toHaveTextContent('No post-mortems yet')
    await user.click(screen.getByRole('link', { name: 'See open positions' }))
    expect(where()).toBe('/portfolio/positions')
  })

  it('a filter that matches nothing says so, not "no entries yet"', async () => {
    fakeBackend({ entries: [WIN] })
    renderJournal('/journal?tag=setup%3Abreakout')
    expect(await screen.findByTestId('journal-none')).toHaveTextContent('No post-mortems match')
  })

  it('a failed load offers Try again', async () => {
    fakeBackend({ fail: true })
    renderJournal()
    expect(await screen.findByTestId('journal-error')).toHaveTextContent('The journal didn’t load')
  })
})

describe('Journal entry', () => {
  it('opens an entry with expected, what happened and the lesson; its proposal link goes to the War Room message', async () => {
    fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderJournal()
    await waitFor(() => expect(screen.getAllByTestId('journal-row')).toHaveLength(2))
    await user.click(rowOf('BTC'))
    await waitFor(() => expect(where()).toBe(`/journal?entry=${WIN.id}`))

    const doc = await screen.findByTestId('journal-entry')
    expect(doc).toHaveAttribute('data-id', WIN.id)
    expect(within(doc).getByTestId('journal-outcome')).toHaveTextContent('WonClosed at TP2')
    expect(within(doc).getByTestId('journal-lesson')).toHaveTextContent('let the take-profit work, do not trail early')

    const table = within(doc).getByTestId('journal-compare')
    expect(within(table).getByRole('columnheader', { name: 'Expected' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'What happened' })).toBeInTheDocument()
    const entryRow = table.querySelector('[data-row="entry"]')!
    expect(entryRow).toHaveTextContent('100.00')
    expect(entryRow).toHaveTextContent('100.50 average')
    const exitRow = table.querySelector('[data-row="exit"]')!
    expect(exitRow).toHaveTextContent('TP1 105.00')
    expect(exitRow).toHaveTextContent('110.00 average')
    expect(table.querySelector('[data-row="result"]')).toHaveTextContent('+1.5R expected')
    expect(table.querySelector('[data-row="result"]')).toHaveTextContent('+1.69R')
    expect(table.querySelector('[data-row="tp1"]')).toHaveTextContent('55% likely')
    expect(table.querySelector('[data-row="tp1"]')).toHaveTextContent('Yes')
    expect(table.querySelector('[data-row="invalid"]')).toHaveTextContent('1H close below 96')

    // the agents named, and each voter's call marked right or wrong
    expect(within(doc).getByTestId('journal-agents')).toHaveTextContent('Strategist')
    expect(doc.querySelector('.jr-vote-res.is-right')).toHaveTextContent('Right')
    expect(doc.querySelector('.jr-vote-res.is-wrong')).toHaveTextContent('Wrong')

    await user.click(within(doc).getByRole('link', { name: 'Open the proposal in the War Room' }))
    expect(where()).toBe(`/warroom?proposal=${WIN.proposal_id}`)
  })

  it('a tag in the entry filters the list by it', async () => {
    fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderJournal(`/journal?entry=${LOSS.id}`)
    const doc = await screen.findByTestId('journal-entry')
    expect(within(doc).getByTestId('journal-lesson')).toHaveTextContent('Lesson still being written')
    await user.click(within(doc).getByRole('link', { name: 'Breakout' }))
    await waitFor(() => expect(where()).toBe('/journal?tag=setup%3Abreakout'))
  })

  it('an entry of the other env (404) says it is not in this journal', async () => {
    fakeBackend()
    renderJournal('/journal?entry=00000000-0000-4000-8000-00000000dead')
    expect(await screen.findByTestId('journal-notfound')).toHaveTextContent('This post-mortem isn’t in the demo journal')
  })
})

describe('Journal model', () => {
  it('reads filters from the URL and turns them into ATL-200 parameters in Baghdad days', () => {
    const f = readFilters(new URLSearchParams('inst=xau-usdt-swap&agent=historian&tag=setup:breakout&outcome=loss&range=7d'))
    expect(f).toEqual({ ...BLANK, inst: 'XAU-USDT-SWAP', agent: 'historian', tag: 'setup:breakout', outcome: 'loss', range: '7d' })
    expect(apiParams(f, NOW).toString()).toBe('inst_id=XAU-USDT-SWAP&agent=historian&tag=setup%3Abreakout&outcome=loss&from=2026-10-22&to=2026-10-28')
    expect(readFilters(new URLSearchParams('outcome=bogus&range=custom&from=2026-10-30&to=2026-10-01'))).toEqual(BLANK)
  })

  it('labels how a trade closed from close_reason, the targets and the best point', () => {
    expect(closeLabel(WIN)).toBe('tp2')
    expect(closeLabel(LOSS)).toBe('stop')
    expect(closeLabel({ ...WIN, data: { ...WIN.data, actual: { ...WIN.data.actual, close_reason: 'manual' } } })).toBe('owner')
    expect(closeLabel({ ...WIN, data: { ...WIN.data, actual: { ...WIN.data.actual, close_reason: 'trail_stop' } } })).toBe('pm')
    expect(tp1Hit(WIN)).toBe(true)
    expect(tp1Hit(LOSS)).toBe(false)
  })

  it('outcome, tags, votes, weeks and the mini ladder', () => {
    expect(outcomeOf({ ...WIN, data: {}, tags: [] })).toBe('scratch')
    expect(outcomeOf({ ...WIN, data: { realised_r: '-0.4' }, tags: [] })).toBe('loss')
    expect(displayTags(WIN.tags)).toEqual(['setup:pullback_to_support', 'asset:crypto', 'regime:trend', 'catalyst:none'])
    expect(voteRight('skip', 'loss')).toBe(true)
    expect(voteRight('take', 'loss')).toBe(false)
    expect(voteRight('take', 'scratch')).toBeNull()
    // Sunday 18:00 Baghdad (15:00 UTC) closes a week
    expect(new Date(weekEnd(Date.parse('2026-10-25T14:59:00Z'))).toISOString()).toBe('2026-10-25T15:00:00.000Z')
    expect(new Date(weekEnd(Date.parse('2026-10-25T15:00:00Z'))).toISOString()).toBe('2026-11-01T15:00:00.000Z')
    const g = miniGeometry(WIN)!
    expect(g.loss[0]).toBeLessThan(g.zero)
    expect(g.exit).toBeGreaterThan(g.zero)
    expect(g.band![0]).toBeLessThan(g.zero)
  })
})
