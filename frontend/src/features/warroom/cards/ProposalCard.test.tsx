import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../../i18n'
import en from '../../../i18n/locales/en/proposal.json'
import type { Lang } from '../../../lib/format'
import { DA_MESSAGE_ID, PROPOSAL_ID, proposalDetail, proposalMessage, sentDetail } from '../fixtures/proposal'
import { Message } from '../Message'
import { proposalKey, stakeOf, type ProposalDetail } from './proposal'
import { editErrors } from './ProposalSheets'

/* ATL-164: the War Room proposal card (brief §12, §7.9 step 5; design v11 wr-proposal). */

const NOW = new Date('2026-10-28T11:32:00Z')

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

type Handler = (init: RequestInit | undefined, url: URL) => Response | undefined

/** A fake Laravel: GET /api/proposals/{id} answers `state.detail`; other routes from `routes`. */
function backend(state: { detail: ProposalDetail }, routes: Record<string, Handler> = {}) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    const method = init?.method ?? 'GET'
    if (url.pathname === '/sanctum/csrf-cookie') return reply(204)
    const route = routes[`${method} ${url.pathname}`]
    if (route) {
      const r = route(init, url)
      if (r) return r
    }
    if (method === 'GET' && url.pathname === `/api/proposals/${PROPOSAL_ID}`) return reply(200, { data: state.detail })
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const calls = (fetch: ReturnType<typeof backend>, method: string, path: string) =>
  fetch.mock.calls.filter(([input, init]) => new URL(String(input)).pathname === path && (init?.method ?? 'GET') === method)

function draw(lang: Lang = 'en', onJump = vi.fn()) {
  void i18n.changeLanguage(lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const noop = () => {}
  const router = createMemoryRouter([
    {
      path: '/',
      element: <Message message={proposalMessage()} prev={undefined} parent={undefined} lang={lang} active={false} tabbable flash={false} onJump={onJump} onReply={noop} onMenuChange={noop} />,
    },
  ])
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { qc, onJump }
}

const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
const card = () => screen.getByTestId('pr-card')

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  void i18n.changeLanguage('en')
})

describe('proposal card', () => {
  it('awaiting_owner: the four actions, the countdown, the plan, the stake and the strongest objection', async () => {
    backend({ detail: proposalDetail() })
    const { onJump } = draw()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    const c = within(card())
    expect(c.getByTestId('pr-approve')).toHaveTextContent('Approve')
    expect(c.getByTestId('pr-reject')).toHaveTextContent('Reject')
    expect(c.getByTestId('pr-edit')).toHaveTextContent('Edit')
    expect(c.getByTestId('pr-ask')).toHaveTextContent('Ask the team')
    // 11:32:00 → 11:38:00: six minutes left, counting down.
    expect(c.getByTestId('pr-countdown')).toHaveTextContent('6:00 left')
    expect(c.getByRole('timer')).toBeInTheDocument()
    await vi.advanceTimersByTimeAsync(2000)
    expect(c.getByTestId('pr-countdown')).toHaveTextContent('5:58 left')
    expect(c.getByTestId('pr-band')).toHaveTextContent('Approve by 14:38')

    expect(c.getByTestId('pr-status')).toHaveTextContent('Awaiting your approval')
    expect(c.getByTestId('pr-dir')).toHaveTextContent('Long')
    expect(c.getByText('Proposal, version', { exact: false })).toHaveTextContent('Proposal, version 2')
    const ladder = c.getByTestId('ladder')
    expect(within(ladder).getByTestId('ld-entry')).toHaveTextContent('248.60–250.20')
    expect(within(ladder).getByTestId('ld-stop')).toHaveTextContent('241.80')
    expect(within(ladder).getAllByTestId('ld-target').map((x) => x.textContent)).toEqual(['TP1258.5050% at +1.2R', 'TP2264.6030% at +2.0R', 'TP3272.2020% at +3.0R'])
    expect(c.getByTestId('pr-odds')).toHaveTextContent('52% to reach TP1 before the stop')
    expect(c.getByTestId('pr-notional')).toHaveTextContent('$2,997.79')
    expect(c.getByTestId('pr-size')).toHaveTextContent('12.02 contracts at 3×, margin $999.27')
    // 2,997.79 × (0.030473… + 2 × 0.0005) = 94.35, of 100,000 sizing equity.
    expect(c.getByTestId('pr-risk')).toHaveTextContent('$94.35')
    expect(card()).toHaveTextContent('0.09% of sizing equity')
    expect(c.getByTestId('pr-why')).toHaveTextContent('Spot SOL ETF approval')
    expect(c.getByTestId('pr-da-rating')).toHaveTextContent('Risk 2 of 5')
    expect(c.getByTestId('pr-da-objection')).toHaveTextContent('The BTC long is 0.82 correlated')
    await user().click(c.getByText('Read the review'))
    expect(onJump).toHaveBeenCalledWith(DA_MESSAGE_ID)
  })

  it('an approved (sent) proposal renders no actions and no countdown, and says what happened', async () => {
    backend({ detail: sentDetail() })
    draw()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'sent'))
    const c = within(card())
    expect(c.queryByTestId('pr-actions')).toBeNull()
    expect(c.queryByTestId('pr-approve')).toBeNull()
    expect(c.queryByTestId('pr-reject')).toBeNull()
    expect(c.queryByTestId('pr-edit')).toBeNull()
    expect(c.queryByTestId('pr-ask')).toBeNull()
    expect(c.queryByTestId('pr-countdown')).toBeNull()
    expect(c.getByTestId('pr-status')).toHaveTextContent('Sent to OKX')
    expect(c.getByTestId('pr-outcome')).toHaveTextContent('Approved by you at 14:33. Sent to OKX at 14:33, limit 249.40.')
  })

  it('autonomy 1: an "advice only" badge and no Approve', async () => {
    backend({ detail: proposalDetail({ status: 'advise_only', status_reason: 'advise_only', actions: { approve: false, approve_blocked_reason: 'not_awaiting_owner', edit: false, reject: true, ask_team: true } }) })
    draw()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'advice'))
    const c = within(card())
    expect(c.getByTestId('pr-status')).toHaveTextContent('Advice only')
    expect(c.queryByTestId('pr-approve')).toBeNull()
    expect(c.queryByTestId('pr-countdown')).toBeNull()
    expect(c.getByTestId('pr-ask')).toBeInTheDocument()
  })

  it('Reject: submit stays disabled without a reason, and with one it POSTs reject', async () => {
    const state = { detail: proposalDetail() }
    const fetch = backend(state, {
      [`POST /api/proposals/${PROPOSAL_ID}/reject`]: (init) => {
        const { reason } = JSON.parse(String(init?.body)) as { reason: string }
        state.detail = proposalDetail({ status: 'rejected', status_reason: 'owner_rejected', owner_note: reason, decided_by: 'owner', decided_at: '2026-10-28T11:32:30.000000Z' })
        return reply(200, { ok: true, proposal: state.detail })
      },
    })
    draw()
    const u = user()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    await u.click(within(card()).getByTestId('pr-reject'))
    const submit = await screen.findByTestId('pr-reject-submit')
    expect(submit).toBeDisabled()
    await u.type(screen.getByTestId('pr-reject-text'), '   ')
    expect(submit).toBeDisabled()
    await u.click(submit)
    expect(calls(fetch, 'POST', `/api/proposals/${PROPOSAL_ID}/reject`)).toHaveLength(0)

    await u.clear(screen.getByTestId('pr-reject-text'))
    await u.type(screen.getByTestId('pr-reject-text'), 'CPI in an hour.')
    expect(submit).toBeEnabled()
    await u.click(submit)
    await waitFor(() => expect(calls(fetch, 'POST', `/api/proposals/${PROPOSAL_ID}/reject`)).toHaveLength(1))
    expect(JSON.parse(String(calls(fetch, 'POST', `/api/proposals/${PROPOSAL_ID}/reject`)[0]![1]!.body))).toEqual({ reason: 'CPI in an hour.' })
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'rejected'))
    expect(within(card()).queryByTestId('pr-actions')).toBeNull()
    expect(within(card()).getByTestId('pr-reject-reason')).toHaveTextContent('CPI in an hour.')
  })

  it('Edit: a buy stop moved below its current value shows "stops only tighten" and is not sent', async () => {
    const fetch = backend({ detail: proposalDetail() })
    draw()
    const u = user()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    await u.click(within(card()).getByTestId('pr-edit'))
    const stop = (await screen.findAllByRole('textbox')).find((x) => x.getAttribute('data-testid')?.endsWith('-stop'))!
    expect(stop).toHaveValue('241.80')
    await u.clear(stop)
    await u.type(stop, '240.50')
    const expected = en.err.stopWiden.replace('{{stop}}', '241.80')
    expect(screen.getByText(expected)).toBeInTheDocument()
    expect(stop).toHaveAttribute('aria-invalid', 'true')
    const save = screen.getByTestId('pr-edit-save')
    expect(save).toBeDisabled()
    await u.click(save)
    await u.type(stop, '{Enter}')
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)

    // Tightening is allowed and goes to PATCH with only the stop.
    await u.clear(stop)
    await u.type(stop, '243.00')
    expect(screen.queryByText(expected)).toBeNull()
    expect(save).toBeEnabled()
  })

  it('Edit sends only what changed, a tighter stop, and the new version replaces the card', async () => {
    const state = { detail: proposalDetail() }
    const v3 = proposalDetail({ id: 'p-v3', version: 3, author: 'owner', parent_id: PROPOSAL_ID, stop: '243', risk_check: { ...proposalDetail().risk_check!, created_at: '2026-10-28T11:32:20.000000Z' } })
    const fetch = backend(state, {
      [`PATCH /api/proposals/${PROPOSAL_ID}`]: () => {
        state.detail = proposalDetail({
          status: 'cancelled',
          status_reason: 'revised',
          actions: { approve: false, approve_blocked_reason: 'not_awaiting_owner', edit: false, reject: false, ask_team: true },
          versions: [...proposalDetail().versions!, { id: 'p-v3', version: 3, author: 'owner', status: 'awaiting_owner', status_reason: null, stop: '243', size_suggestion_pct: '3', take_profits: v3.take_profits, created_at: '2026-10-28T11:32:20.000000Z' }],
        })
        return reply(200, { ok: true, proposal: v3, risk_check: { result: 'resized', decision: 'awaiting_owner' } })
      },
      'GET /api/proposals/p-v3': () => reply(200, { data: v3 }),
    })
    draw()
    const u = user()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    await u.click(within(card()).getByTestId('pr-edit'))
    const stop = (await screen.findAllByRole('textbox')).find((x) => x.getAttribute('data-testid')?.endsWith('-stop'))!
    await u.clear(stop)
    await u.type(stop, '243.00')
    await u.click(screen.getByTestId('pr-edit-save'))
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1))
    expect(JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!.body))).toEqual({ stop: '243.00' })
    await waitFor(() => expect(card()).toHaveAttribute('data-proposal-id', 'p-v3'))
    expect(within(card()).getByTestId('pr-edited')).toHaveTextContent('Version 3 is your edit')
    expect(within(card()).getByTestId('pr-approve')).toBeEnabled()
  })

  it('a 409 on Approve refetches the proposal and shows the server’s reason', async () => {
    const state = { detail: proposalDetail() }
    const fetch = backend(state, {
      [`POST /api/proposals/${PROPOSAL_ID}/approve`]: () => {
        state.detail = proposalDetail({
          status: 'rejected',
          status_reason: 'risk_rejected',
          decided_by: 'owner',
          decided_at: '2026-10-28T11:32:05.000000Z',
          actions: { approve: false, approve_blocked_reason: 'not_awaiting_owner', edit: false, reject: false, ask_team: true },
        })
        return reply(409, {
          ok: false,
          error: 'execution_refused',
          message: 'The trade was not sent. The reason is shown with the proposal.',
          execution: { ok: false, status: 'rejected', reason: 'risk_rejected', detail: 'Rejected by max_daily_loss.' },
          proposal: state.detail,
        })
      },
    })
    draw()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    const gets = () => calls(fetch, 'GET', `/api/proposals/${PROPOSAL_ID}`).length
    const before = gets()
    await user().click(within(card()).getByTestId('pr-approve'))
    const notice = await within(card()).findByTestId('pr-conflict')
    expect(notice).toHaveTextContent(en.c409.title)
    expect(notice).toHaveTextContent(en.why409.risk_rejected)
    await waitFor(() => expect(gets()).toBeGreaterThan(before))
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'rejected'))
    expect(within(card()).queryByTestId('pr-approve')).toBeNull()
  })

  it('a 409 with a reason the locale does not know shows the server’s own words', async () => {
    backend({ detail: proposalDetail() }, {
      [`POST /api/proposals/${PROPOSAL_ID}/approve`]: () =>
        reply(409, { ok: false, error: 'execution_refused', message: 'x', execution: { reason: 'instrument_suspended', detail: 'OKX suspended SOL-USDT-SWAP.' } }),
    })
    draw()
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    await user().click(within(card()).getByTestId('pr-approve'))
    expect(await within(card()).findByTestId('pr-conflict')).toHaveTextContent('OKX suspended SOL-USDT-SWAP.')
  })

  it('the buttons leave when the status changes elsewhere (the query is refreshed)', async () => {
    const state = { detail: proposalDetail() }
    backend(state)
    const { qc } = draw()
    await waitFor(() => expect(within(card()).getByTestId('pr-approve')).toBeInTheDocument())
    state.detail = sentDetail()
    await qc.invalidateQueries({ queryKey: proposalKey(PROPOSAL_ID) })
    await waitFor(() => expect(within(card()).queryByTestId('pr-actions')).toBeNull())
  })

  it('Arabic: words translated, figures stay left-to-right islands', async () => {
    backend({ detail: proposalDetail() })
    draw('ar')
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'awaiting_owner'))
    const c = within(card())
    expect(c.getByTestId('pr-approve')).toHaveTextContent('موافقة')
    expect(c.getByTestId('pr-status')).toHaveTextContent('بانتظار موافقتك')
    const fig = c.getByTestId('pr-countdown').querySelector('bdi')!
    expect(fig).toHaveAttribute('dir', 'ltr')
    expect(fig).toHaveTextContent('6:00')
  })
})

describe('edit rules', () => {
  const t = (k: string, o?: Record<string, unknown>) => `${k}${o ? JSON.stringify(o) : ''}`
  const base = { size: '3000', stop: '241.80', tps: [{ price: '258.50', pct: '50' }, { price: '264.60', pct: '30' }, { price: '272.20', pct: '20' }] }
  const ctx = { side: 'buy' as const, zone: ['248.60', '250.20'] as [string, string], widest: '241.8', cap: '3000', withSize: true }
  it('a sell stop may only fall', () => {
    const sell = { ...ctx, side: 'sell' as const, widest: '258' }
    const f = { ...base, stop: '259', tps: [{ price: '240', pct: '100' }] }
    expect(editErrors(f, sell, t, String).stop).toMatch(/^err\.stopWiden/)
    expect(editErrors({ ...f, stop: '255' }, sell, t, String).stop).toBeUndefined()
  })
  it('shares must total 100 and size stays under the per-trade cap', () => {
    expect(editErrors({ ...base, tps: [{ price: '258.5', pct: '50' }, { price: '264.6', pct: '30' }, { price: '272.2', pct: '10' }] }, ctx, t, String).sum).toMatch(/^err\.sum/)
    expect(editErrors({ ...base, size: '3,500' }, ctx, t, String).size).toMatch(/^err\.sizeMax/)
    expect(editErrors({ ...base, size: '٢٥٠٠' }, ctx, t, String)).toEqual({})
  })
  it('the stake: risk at the stop from the checked notional and the fee both ways', () => {
    const s = stakeOf(proposalDetail())
    expect(s.risk).toBe('94.35')
    expect(s.riskPct).toBe('0.09')
    expect(s.perTradeCap).toBe('3000')
  })
})
