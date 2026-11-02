import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import i18n from '../../i18n'
import type { WarroomMessage, WarroomThread } from './api'
import { proposalKey, type ProposalDetail } from './cards/proposal'
import { PROPOSAL_ID, proposalDetail, proposalMessage } from './fixtures/proposal'
import { upsertMessage } from './live'
import { isReplayable, lastReplayDay, replayKey, shiftDay } from './replay'
import warroom from './routes'

/*
 * ATL-206: War Room Replay. A fake Laravel serves GET /api/warroom/replay?date= (the
 * WarroomController::replay contract: one Baghdad day, oldest first) and the live War Room's
 * endpoints, so the toolbar's way in can be followed too.
 */

type Listener = (payload: unknown) => void
const listeners = new Map<string, Listener[]>()
vi.mock('../../lib/echo', () => ({
  getEcho: () => ({
    private: () => ({
      listen: (event: string, fn: Listener) => {
        listeners.set(event, [...(listeners.get(event) ?? []), fn])
      },
      stopListening: (event: string) => listeners.delete(event),
    }),
  }),
  hasConnected: () => true,
  reconnectEcho: () => {},
  useLiveStatus: () => 'connected',
}))

const NOW = Date.parse('2026-10-29T08:00:00Z') // 11:00 Baghdad, 29 Oct
const MAIN: WarroomThread = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: null, created_at: '2026-07-14T15:02:00.000000Z' }

function msg(id: string, created_at: string, over: Partial<WarroomMessage> = {}): WarroomMessage {
  return {
    id,
    thread_id: 'main-1',
    author_type: 'agent',
    author: 'historian',
    kind: 'text',
    content_md: `Message ${id}`,
    card: null,
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    created_at,
    ...over,
  }
}

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function fakeBackend(days: Record<string, WarroomMessage[] | 'fail'>, detail?: ProposalDetail) {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/warroom/replay') {
      const date = url.searchParams.get('date') ?? ''
      const day = days[date] ?? []
      if (day === 'fail') return reply(500, { message: 'Server Error' })
      return reply(200, { data: day, meta: { date, timezone: 'Asia/Baghdad', from: '', to: '' } })
    }
    if (url.pathname === '/api/warroom/threads') return reply(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return reply(200, { data: [], meta: { next_cursor: null } })
    if (url.pathname === '/api/portfolio/positions') return reply(200, { data: { taken_at: '2026-10-29T07:00:00.000000Z', items: [] } })
    if (url.pathname.startsWith('/api/proposals/') && detail) return reply(200, { data: detail })
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const replayCalls = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.map(([input]) => new URL(String(input))).filter((u) => u.pathname === '/api/warroom/replay')

function renderAt(path: string, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const router = createMemoryRouter(
    warroom.routes.map((r) => ({
      ...r,
      path: `/${r.path}`,
      element: (
        <>
          <div data-slot="toolbar-actions" />
          {r.element}
        </>
      ),
    })),
    { initialEntries: [path] },
  )
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router, client }
}

const feedIds = () => [...screen.getByTestId('rp-feed').querySelectorAll<HTMLElement>('[data-msg-id]')].map((el) => el.dataset.msgId)

beforeEach(async () => {
  listeners.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
  // jsdom has no layout: give the virtualised feed a viewport and its rows a height.
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(800)
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(720)
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('Replay days (Baghdad)', () => {
  it('knows which days are finished, near midnight UTC', () => {
    const late = Date.parse('2026-10-29T21:30:00Z') // 00:30 on 30 Oct in Baghdad
    expect(lastReplayDay(late)).toBe('2026-10-29')
    expect(isReplayable('2026-10-29', late)).toBe(true)
    expect(isReplayable('2026-10-30', late)).toBe(false)
    expect(isReplayable('2026-11-02', late)).toBe(false)
    expect(isReplayable('2026-02-31', late)).toBe(false)
    expect(isReplayable('yesterday', late)).toBe(false)
    expect(shiftDay('2026-10-31', 1)).toBe('2026-11-01')
    expect(shiftDay('2026-11-01', -1)).toBe('2026-10-31')
  })
})

describe('War Room Replay', () => {
  it('opens from the live toolbar on yesterday, and a chosen date requests that day and renders it in order', async () => {
    const fetch = fakeBackend({
      '2026-10-28': [msg('y-1', '2026-10-27T21:05:00.000000Z')],
      '2026-10-25': [
        msg('a-1', '2026-10-24T21:00:00.000000Z', { author_type: 'human', author: 'Owner', content_md: 'Good morning team' }),
        msg('a-2', '2026-10-25T05:00:00.000000Z', { content_md: 'Regime table posted' }),
        msg('a-3', '2026-10-25T20:59:00.000000Z', { author: 'market_scout', content_md: 'Last word of the day' }),
      ],
    })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { router } = renderAt('/warroom')

    await user.click(await screen.findByTestId('wr-replay-open'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/warroom/replay/2026-10-28'))
    expect(await screen.findByTestId('rp-banner')).toHaveTextContent('Replay, Wednesday 28 October')
    await waitFor(() => expect(feedIds()).toEqual(['y-1']))

    await user.click(screen.getByTestId('rp-date'))
    const cal = await screen.findByTestId('rp-calendar')
    await user.click(within(cal).getByRole('button', { name: 'Sunday 25 October' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/warroom/replay/2026-10-25'))
    await waitFor(() => expect(feedIds()).toEqual(['a-1', 'a-2', 'a-3']))
    const dates = replayCalls(fetch).map((u) => [u.searchParams.get('date'), u.searchParams.get('env')])
    expect(dates).toEqual([
      ['2026-10-28', 'demo'],
      ['2026-10-25', 'demo'],
    ])
    expect(screen.getByTestId('rp-feed')).toHaveTextContent(/Good morning team.*Regime table posted.*Last word of the day/)
    expect(screen.getByTestId('rp-banner')).toHaveTextContent('Sunday 25 October')
  })

  it('is read-only: no Approve/Reject/Edit, no composer, no reply, and a live message never appends', async () => {
    const day = '2026-10-28'
    // The proposal is awaiting the owner now, and the live War Room has it cached: still no actions.
    const awaiting = proposalDetail({ approval_expires_at: '2026-10-29T08:10:00.000000Z' })
    const prop = proposalMessage({ thread_id: 'main-1' })
    const answer = msg('r-1', '2026-10-28T11:30:00.000000Z', { reasoning_md: 'Regime first: range for 3 days.' })
    fakeBackend({ [day]: [prop, answer] }, awaiting)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(proposalKey(PROPOSAL_ID), awaiting)
    renderAt(`/warroom/replay/${day}`, client)

    await waitFor(() => expect(feedIds()).toEqual(['m-prop-412', 'r-1']))
    const card = screen.getByTestId('rp-feed').querySelector<HTMLElement>('[data-msg-id="m-prop-412"]')!
    expect(within(card).getByTestId('pr-readonly')).toHaveTextContent('As posted. Actions are off in Replay.')
    expect(within(card).queryByTestId('pr-actions')).toBeNull()
    expect(within(card).queryByTestId('pr-band')).toBeNull()
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^reject/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^edit/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /reply/i })).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(document.querySelector('.composer')).toBeNull()
    expect(screen.getByTestId('rp-foot')).toHaveTextContent('Replay is read-only')

    // Reasoning disclosures still open.
    const toggle = within(screen.getByTestId('rp-feed').querySelector<HTMLElement>('[data-msg-id="r-1"]')!).getByTestId('wr-reason-toggle')
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    // A live warroom event, whether through a socket listener or the live feed's cache upsert.
    const late = msg('live-1', '2026-10-28T12:00:00.000000Z', { content_md: 'Arrived live' })
    act(() => {
      for (const fn of listeners.get('.warroom.message') ?? []) fn(late)
      upsertMessage(client, 'demo', late)
    })
    expect(feedIds()).toEqual(['m-prop-412', 'r-1'])
    expect(screen.queryByText('Arrived live')).toBeNull()
    expect(client.getQueryData<{ data: WarroomMessage[] }>(replayKey('demo', day))?.data.map((m) => m.id)).toEqual(['m-prop-412', 'r-1'])
  })

  it('lists the day’s moments and jumps to one', async () => {
    const day = '2026-10-28'
    fakeBackend({ [day]: [msg('t-1', '2026-10-28T05:00:00.000000Z'), proposalMessage({ thread_id: 'main-1' })] }, proposalDetail())
    renderAt(`/warroom/replay/${day}`)
    const moments = await screen.findByTestId('rp-moments')
    const items = within(moments).getAllByTestId('rp-moment')
    expect(items).toHaveLength(1)
    expect(items[0]).toHaveTextContent('Proposal')
    expect(screen.getByTestId('rp-inspector')).toHaveTextContent('2 messages')
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(items[0]!)
    await waitFor(() => expect(document.activeElement?.getAttribute('data-msg-id')).toBe('m-prop-412'))
  })

  it('disables today and later days in Baghdad time, just after midnight in Baghdad', async () => {
    vi.setSystemTime(Date.parse('2026-10-29T21:30:00Z')) // 00:30 on Fri 30 Oct in Baghdad, still 29 Oct in UTC
    fakeBackend({})
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { router } = renderAt('/warroom/replay/2026-10-30')
    // Today is not a replay day: the URL falls back to yesterday (29 Oct), which UTC would call today.
    await waitFor(() => expect(router.state.location.pathname).toBe('/warroom/replay/2026-10-29'))
    expect(await screen.findByTestId('rp-next-day')).toBeDisabled()

    await user.click(screen.getByTestId('rp-date'))
    const cal = await screen.findByTestId('rp-calendar')
    const dayBtn = (n: number) => within(cal).getByText(String(n), { selector: 'bdi' }).closest('button')!
    expect(dayBtn(29)).not.toHaveAttribute('aria-disabled')
    expect(dayBtn(29)).toHaveAttribute('aria-pressed', 'true')
    expect(dayBtn(30)).toHaveAttribute('aria-disabled', 'true')
    expect(dayBtn(31)).toHaveAttribute('aria-disabled', 'true')
    expect(dayBtn(1)).not.toHaveAttribute('aria-disabled')
    expect(within(cal).getByTestId('rp-cal-next')).toBeDisabled()

    // Clicking a disabled day does nothing; the arrows stop at yesterday.
    await user.click(dayBtn(30))
    expect(router.state.location.pathname).toBe('/warroom/replay/2026-10-29')
    dayBtn(29).focus()
    await user.keyboard('{ArrowRight}')
    expect(document.activeElement).toBe(dayBtn(29))
    await user.keyboard('{ArrowLeft}')
    expect(document.activeElement).toBe(dayBtn(28))
  })

  it('shows an empty state for a silent day and an error state when the day fails', async () => {
    fakeBackend({ '2026-10-20': [], '2026-10-21': 'fail' })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { router } = renderAt('/warroom/replay/2026-10-20')
    expect(await screen.findByTestId('rp-empty')).toHaveTextContent('Nothing was said on Tuesday 20 October')
    expect(screen.getByTestId('rp-pick')).toBeInTheDocument()
    expect(screen.getByTestId('rp-inspector')).toHaveTextContent('Nothing was proposed')
    await user.click(screen.getByTestId('rp-next-day'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/warroom/replay/2026-10-21'))
    expect(await screen.findByTestId('rp-error')).toHaveTextContent('This day didn’t load')
    await user.click(screen.getByTestId('rp-back-live'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/warroom'))
  })
})

/*
 * ATL-207: the day line. Fixture times sit around the UTC/Baghdad offset: 00:00 Baghdad on
 * 28 Oct is 21:00 UTC on 27 Oct, and 12:00 Baghdad is 09:00 UTC.
 */
describe('Replay day line', () => {
  const DAY = '2026-10-28'
  const AROUND_NOON = [
    msg('d-0000', '2026-10-27T21:00:00.000000Z', { content_md: 'Midnight in Baghdad, still 27 Oct in UTC' }),
    msg('d-1159', '2026-10-28T08:59:59.000000Z', { content_md: 'One second before noon' }),
    msg('d-1200', '2026-10-28T09:00:00.000000Z', { content_md: 'Noon exactly' }),
    msg('d-1201', '2026-10-28T09:00:01.000000Z', { content_md: 'One second after noon' }),
    msg('d-1500', '2026-10-28T12:00:00.000000Z', { content_md: 'Afternoon' }),
  ]
  /** The track is 1,440 px wide in these tests: one pixel per minute of the day. */
  const trackRect = () =>
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const w = this.matches('[data-testid="rp-track"]') ? 1440 : 100
      return { x: 0, y: 0, left: 0, top: 0, right: w, bottom: 34, width: w, height: 34, toJSON: () => ({}) } as DOMRect
    })
  const clock = () => screen.getByTestId('rp-time').textContent ?? ''
  const seconds = (hms: string) => hms.split(':').reduce((s, p) => s * 60 + Number(p), 0)

  it('pure helpers: shown at 12:00 Baghdad, steps and the pointer maths in both directions', async () => {
    const { shownCount, stepIndex, messageTimes, atFromPointer, densityBuckets } = await import('./replayTime')
    const { dayStartMs } = await import('./replay')
    const start = dayStartMs(DAY)
    expect(new Date(start).toISOString()).toBe('2026-10-27T21:00:00.000Z')
    const times = messageTimes(AROUND_NOON)
    expect(shownCount(times, start + 12 * 3_600_000)).toBe(3)
    expect(shownCount(times, start - 1)).toBe(0)
    expect(stepIndex(times, start + 12 * 3_600_000, 1)).toBe(3)
    expect(stepIndex(times, start + 12 * 3_600_000, -1)).toBe(1)
    expect(stepIndex(times, start, -1)).toBeNull()
    expect(atFromPointer(360, { left: 0, width: 1440 }, false, start)).toBe(start + 6 * 3_600_000)
    expect(atFromPointer(360, { left: 0, width: 1440 }, true, start)).toBe(start + 18 * 3_600_000)
    expect(densityBuckets(times, start).reduce((a, b) => a + b, 0)).toBe(5)
  })

  it('setting the scrubber to 12:00 Baghdad shows only messages created at or before 12:00 Baghdad', async () => {
    fakeBackend({ [DAY]: AROUND_NOON })
    trackRect()
    renderAt(`/warroom/replay/${DAY}`)
    await waitFor(() => expect(feedIds()).toEqual(AROUND_NOON.map((m) => m.id)))
    expect(clock()).toBe('23:59:59')

    const track = screen.getByTestId('rp-track')
    act(() => {
      fireEvent.pointerDown(track, { clientX: 720, button: 0, pointerId: 1 })
      fireEvent.pointerUp(track, { clientX: 720, pointerId: 1 })
    })
    expect(clock()).toBe('12:00:00')
    await waitFor(() => expect(feedIds()).toEqual(['d-0000', 'd-1159', 'd-1200']))
    expect(screen.getByTestId('rp-count')).toHaveTextContent('3 of 5 messages so far')
    expect(screen.getByTestId('rp-head')).toHaveAttribute('aria-valuenow', '720')

    // Dragging before the first message: the feed says when talk began.
    act(() => {
      fireEvent.pointerDown(track, { clientX: 0, button: 0, pointerId: 1 })
      fireEvent.pointerUp(track, { clientX: 0, pointerId: 1 })
    })
    // 00:00:00 is the first message's own instant, so it is shown.
    await waitFor(() => expect(feedIds()).toEqual(['d-0000']))
  })

  it('shows "Nothing yet" before the first message of the day', async () => {
    fakeBackend({ [DAY]: AROUND_NOON.slice(1) })
    renderAt(`/warroom/replay/${DAY}`)
    const head = await screen.findByTestId('rp-head')
    act(() => head.focus())
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).keyboard('{Home}')
    expect(await screen.findByTestId('rp-before')).toHaveTextContent('Nothing yet. The first message of the day came at 11:59.')
    expect(screen.queryByTestId('rp-feed')).toBeNull()
    const moments = screen.queryAllByTestId('rp-moment')
    moments.forEach((b) => expect(b).toHaveClass('is-future'))
  })

  it('clicking a proposal marker jumps the time to that message and scrolls it into view', async () => {
    const later = msg('t-late', '2026-10-28T15:00:00.000000Z', { content_md: 'Evening' })
    fakeBackend({ [DAY]: [msg('t-1', '2026-10-28T05:00:00.000000Z'), proposalMessage({ thread_id: 'main-1' }), later] }, proposalDetail())
    // jsdom has no scrolling: record where the virtualised feed is asked to scroll.
    const scrollTo = vi.fn(function (this: Element, opts?: ScrollToOptions | number) {
      if (typeof opts === 'object' && typeof opts.top === 'number') (this as HTMLElement).scrollTop = opts.top
    })
    Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, writable: true, value: scrollTo })
    onTestFinished(() => {
      delete (Element.prototype as Partial<Element>).scrollTo
    })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt(`/warroom/replay/${DAY}`)
    await waitFor(() => expect(feedIds()).toContain('t-late'))

    const marker = screen.getAllByTestId('rp-marker').find((b) => b.dataset.kind === 'proposal')!
    expect(marker).toHaveAccessibleName('14:19 Proposal')
    scrollTo.mockClear()
    await user.click(marker)

    expect(clock()).toBe('14:19:00')
    await waitFor(() => expect(feedIds()).toEqual(['t-1', 'm-prop-412']))
    await waitFor(() => expect(document.activeElement?.getAttribute('data-msg-id')).toBe('m-prop-412'))
    expect(scrollTo).toHaveBeenCalled()
    expect(marker).toHaveClass('is-past')
    // The inspector's moment follows the playhead: reached, and current.
    const moment = screen.getByTestId('rp-moment')
    expect(moment).toHaveClass('is-past')
    expect(moment).toHaveAttribute('aria-current', 'step')
  })

  it('plays at 60× (60 s of replay per second), Space toggles play/pause and ArrowRight steps to the next message', async () => {
    fakeBackend({ [DAY]: AROUND_NOON })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt(`/warroom/replay/${DAY}`)
    const head = await screen.findByTestId('rp-head')
    await waitFor(() => expect(feedIds()).toHaveLength(5))
    expect(screen.getByRole('radio', { name: '60×' })).toBeChecked()

    // Home puts the playhead at 00:00; 60× is the default speed.
    act(() => head.focus())
    await user.keyboard('{Home}')
    expect(clock()).toBe('00:00:00')
    const play = screen.getByTestId('rp-play')
    await user.click(play)
    expect(play).toHaveAttribute('aria-pressed', 'true')
    expect(play).toHaveAccessibleName('Pause')
    const t0 = seconds(clock())
    act(() => vi.advanceTimersByTime(1000))
    expect(seconds(clock()) - t0).toBe(60)
    act(() => vi.advanceTimersByTime(2000))
    expect(seconds(clock()) - t0).toBe(180)

    // Space toggles play/pause from anywhere that is not a control.
    act(() => (document.activeElement as HTMLElement | null)?.blur())
    await user.keyboard(' ')
    expect(play).toHaveAttribute('aria-pressed', 'false')
    const paused = clock()
    act(() => vi.advanceTimersByTime(2000))
    expect(clock()).toBe(paused)
    await user.keyboard(' ')
    expect(play).toHaveAttribute('aria-pressed', 'true')
    await user.keyboard(' ')
    expect(play).toHaveAttribute('aria-pressed', 'false')

    // ArrowRight on the playhead steps to the next message (11:59:59), then the next (12:00).
    act(() => head.focus())
    await user.keyboard('{ArrowRight}')
    expect(clock()).toBe('11:59:59')
    await waitFor(() => expect(feedIds()).toEqual(['d-0000', 'd-1159']))
    await user.keyboard('{ArrowRight}')
    expect(clock()).toBe('12:00:00')
    expect(document.activeElement).toBe(head)
    await user.keyboard('{ArrowLeft}')
    expect(clock()).toBe('11:59:59')

    // 10× advances ten seconds per second.
    await user.click(screen.getByRole('radio', { name: '10×' }))
    await user.click(play)
    const t1 = seconds(clock())
    act(() => vi.advanceTimersByTime(1000))
    expect(seconds(clock()) - t1).toBe(10)
  })

  it('in Arabic, ArrowLeft goes forward in time, as the line runs right to left', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend({ [DAY]: AROUND_NOON })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt(`/warroom/replay/${DAY}`)
    const head = await screen.findByTestId('rp-head')
    act(() => head.focus())
    await user.keyboard('{Home}{ArrowLeft}')
    expect(clock()).toBe('11:59:59')
    await user.keyboard('{ArrowRight}')
    expect(clock()).toBe('00:00:00')
  })
})
