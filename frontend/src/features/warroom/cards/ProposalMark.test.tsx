import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../../i18n'
import { PROPOSAL_ID, proposalDetail, proposalMessage } from '../fixtures/proposal'
import { Message } from '../Message'
import { freshMark, type ProposalDetail, type ProposalMark } from './proposal'

/* ATL-188: the proposal card's ladder shows "Now" from a mark at most 30 s old, polled every 10 s
   while the card is on screen and the trade is live (design v12 §13). */

const NOW = new Date('2026-10-28T11:32:00Z')
const DETAIL = `/api/proposals/${PROPOSAL_ID}`
const MARK = `/api/proposals/${PROPOSAL_ID}/mark`

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

/** A fake Laravel: the proposal from `detail()`, the mark from `mark()`, each read at request time. */
function backend(detail: () => ProposalDetail, mark: () => ProposalMark) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    if ((init?.method ?? 'GET') !== 'GET') return reply(404)
    if (url.pathname === DETAIL) return reply(200, { data: detail() })
    if (url.pathname === MARK) return reply(200, { data: mark() })
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const gets = (fetch: ReturnType<typeof backend>, path: string) => fetch.mock.calls.filter(([input]) => new URL(String(input)).pathname === path).length

function draw() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const noop = () => {}
  const router = createMemoryRouter([
    {
      path: '/',
      element: <Message message={proposalMessage()} prev={undefined} parent={undefined} lang="en" active={false} tabbable flash={false} onJump={noop} onReply={noop} onMenuChange={noop} />,
    },
  ])
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const card = () => screen.getByTestId('pr-card')
const loaded = () => waitFor(() => expect(within(card()).getByTestId('ladder')).toBeInTheDocument())
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)))
const noNowAnywhere = () => {
  expect(within(card()).queryAllByTestId('ld-now')).toHaveLength(0)
  expect(card().textContent ?? '').not.toMatch(/\bNow\b/)
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  void i18n.changeLanguage('en')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('proposal card: the price now', () => {
  it('a mark 10 s old draws ld-now on the ladder with the mark price', async () => {
    const at = ago(10_000)
    backend(() => proposalDetail({ mark: '249.95', mark_at: at }), () => ({ inst_id: 'SOL-USDT-SWAP', mark: '249.95', mark_at: at }))
    draw()
    await loaded()
    const full = within(card()).getByTestId('ladder')
    const now = within(full).getByTestId('ld-now')
    expect(now).toHaveTextContent('Now')
    expect(now).toHaveTextContent('249.95')
    expect(full).toHaveClass('has-now-room')
    // the compact rail: Now is its only label
    const compact = within(card()).getByTestId('ladder-compact')
    expect(compact).toHaveClass('is-now-only')
    expect(within(compact).getByTestId('ld-now')).toHaveTextContent('249.95')
    expect(within(compact).queryByTestId('ld-entry')).toBeNull()
  })

  it('a mark 31 s old shows no Now anywhere on the card', async () => {
    const at = ago(31_000)
    backend(() => proposalDetail({ mark: '249.95', mark_at: at }), () => ({ inst_id: 'SOL-USDT-SWAP', mark: '249.95', mark_at: at }))
    draw()
    await loaded()
    noNowAnywhere()
    // the room is kept, so the card's height does not depend on the price
    expect(within(card()).getByTestId('ladder')).toHaveClass('has-now-room')
  })

  it('a null mark shows no Now anywhere on the card', async () => {
    backend(() => proposalDetail({ mark: null, mark_at: null }), () => ({ inst_id: 'SOL-USDT-SWAP', mark: null, mark_at: null }))
    draw()
    await loaded()
    noNowAnywhere()
  })

  it('a mark 25 s old at render is gone 6 s later, with no new fetch', async () => {
    const at = ago(25_000)
    const fetch = backend(() => proposalDetail({ mark: '249.95', mark_at: at }), () => ({ inst_id: 'SOL-USDT-SWAP', mark: '249.95', mark_at: at }))
    draw()
    await loaded()
    await waitFor(() => expect(gets(fetch, MARK)).toBe(1))
    expect(within(card()).getAllByTestId('ld-now').length).toBeGreaterThan(0)
    const before = fetch.mock.calls.length
    await advance(6_000)
    expect(fetch.mock.calls.length).toBe(before)
    noNowAnywhere()
  })

  for (const status of ['awaiting_owner', 'advise_only', 'sent', 'partially_filled'] as const) {
    it(`${status}: the mark is refetched every 10 s and Now stays while each answer is fresh`, async () => {
      const at0 = ago(1_000)
      const fetch = backend(
        () => proposalDetail({ status, mark: '249.95', mark_at: at0, approval_expires_at: status === 'awaiting_owner' ? '2026-10-28T12:30:00Z' : null }),
        () => ({ inst_id: 'SOL-USDT-SWAP', mark: '249.95', mark_at: ago(500) }),
      )
      draw()
      await loaded()
      await waitFor(() => expect(gets(fetch, MARK)).toBe(1))
      for (let i = 2; i <= 5; i++) {
        await advance(10_000)
        await waitFor(() => expect(gets(fetch, MARK)).toBe(i))
        expect(within(within(card()).getByTestId('ladder')).getByTestId('ld-now')).toHaveTextContent('249.95')
      }
      // 40 s after the proposal's own mark: only the polled marks keep Now on screen
      expect(gets(fetch, DETAIL)).toBe(1)

      cleanup()
      const after = gets(fetch, MARK)
      await advance(30_000)
      expect(gets(fetch, MARK)).toBe(after)
    })
  }

  for (const status of ['filled', 'closed', 'rejected', 'expired'] as const) {
    it(`${status}: no mark refetch and no Now`, async () => {
      const at = ago(1_000)
      const fetch = backend(() => proposalDetail({ status, mark: '249.95', mark_at: at, approval_expires_at: null }), () => ({ inst_id: 'SOL-USDT-SWAP', mark: '249.95', mark_at: ago(500) }))
      draw()
      await waitFor(() => expect(card()).toHaveAttribute('data-status', status))
      await advance(30_000)
      expect(gets(fetch, MARK)).toBe(0)
      noNowAnywhere()
    })
  }
})

describe('freshMark', () => {
  const t = Date.parse('2026-10-28T11:32:00Z')
  it('takes the newer of the two marks and drops one older than 30 s', () => {
    expect(freshMark([{ mark: '1', mark_at: '2026-10-28T11:31:40Z' }, { mark: '2', mark_at: '2026-10-28T11:31:55Z' }], t)).toBe('2')
    expect(freshMark([{ mark: '1', mark_at: '2026-10-28T11:31:30Z' }], t)).toBe('1')
    expect(freshMark([{ mark: '1', mark_at: '2026-10-28T11:31:29.999Z' }], t)).toBeNull()
    expect(freshMark([{ mark: null, mark_at: null }, undefined], t)).toBeNull()
  })
})
