import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { autonomyCheck, PM_PROPOSAL_ID, pmActionDetail, pmActionMessage, type PmActionKind } from '../fixtures/proposal'
import { Message } from '../Message'
import type { ProposalDetail } from './proposal'

/*
 * The Portfolio Manager's action card (brief §7.8, ATL-169/184/185; design v18 §4.6; ATL-219
 * criterion 5): a stop change or a close, with the same AutonomyNote built from its own check 7
 * values (on the whole position's notional for a stop change; closes never wait at level 3).
 * See also e2e/warroom-pm-autonomy.spec.ts, which shares these fixtures.
 */

const NOW = new Date('2026-10-28T11:32:00Z')

function backend(state: { detail: ProposalDetail }) {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/sanctum/csrf-cookie') return new Response(null, { status: 204 })
    if (url.pathname === `/api/proposals/${PM_PROPOSAL_ID}`) return new Response(JSON.stringify({ data: state.detail }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    return new Response(JSON.stringify({ message: 'Not found' }), { status: 404, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function draw(message: WarroomMessage, lang: Lang = 'en') {
  void i18n.changeLanguage(lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const noop = () => {}
  const router = createMemoryRouter([
    { path: '/', element: <Message message={message} prev={undefined} parent={undefined} lang={lang} active={false} tabbable flash={false} onJump={noop} onReply={noop} onMenuChange={noop} /> },
  ])
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const card = () => screen.getByTestId('pm-prop-card')
const pm = (kind: PmActionKind) => pmActionMessage(kind)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  void i18n.changeLanguage('en')
})

describe('Portfolio Manager action card (ATL-219 criterion 5)', () => {
  it('level 3 stop change above the ask-above amount: it waits for the owner on the whole position’s notional', async () => {
    const detail = pmActionDetail('amend_stop')
    detail.risk_check!.checks = [autonomyCheck(7, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '163848', ask_above_usd: '3000', note: 'above_ask_threshold' })]
    backend({ detail })
    draw(pm('amend_stop'))
    const c = within(card())
    const note = await c.findByTestId('au-note')
    expect(note).toHaveTextContent('Autonomy 3 asks you.')
    expect(note).toHaveTextContent('The whole position')
    expect(note).toHaveTextContent('$163,848.00')
    expect(note).toHaveTextContent('$3,000.00')
    expect(c.getByTestId('pr-approve')).toBeInTheDocument()
    expect(c.getByTestId('pr-reject')).toBeInTheDocument()
  })

  it('level 3 close: sent because closing never waits, whatever its size', async () => {
    const detail = pmActionDetail('close', { status: 'sent', decided_by: 'autonomy:3', decided_at: '2026-10-28T10:46:40.000000Z' })
    detail.risk_check!.checks = [autonomyCheck(7, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '262830', ask_above_usd: '3000', note: 'risk_reducing' })]
    backend({ detail })
    draw(pm('close'))
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'sent'))
    const c = within(card())
    expect(c.getByTestId('pr-status')).toHaveTextContent('Sent without asking')
    const note = c.getByTestId('au-note')
    expect(note).toHaveTextContent('Autonomy 3 didn’t ask.')
    expect(note).toHaveTextContent('Closing never waits, whatever its size.')
    expect(c.queryByTestId('pr-actions')).toBeNull()
  })

  it('level 2 stop change: Approve and Reject show, with no autonomy note', async () => {
    const detail = pmActionDetail('amend_stop')
    backend({ detail })
    draw(pm('amend_stop'))
    const c = within(card())
    await c.findByTestId('pr-approve')
    expect(c.getByTestId('pr-reject')).toBeInTheDocument()
    expect(c.queryByTestId('au-note')).toBeNull()
  })
})
