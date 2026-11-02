import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { autonomyCheck } from '../fixtures/proposal'
import { Message } from '../Message'
import type { ProposalDetail } from './proposal'

/*
 * The Portfolio Manager's action card (brief §7.8, ATL-169/184/185; design v18 §4.6; ATL-219
 * criterion 5): a stop change or a close, with the same AutonomyNote built from its own check 7
 * values (on the whole position's notional for a stop change; closes never wait at level 3).
 */

const NOW = new Date('2026-10-28T11:32:00Z')
const PM_ID = '7c6b5a40-2e1d-4c9b-8a7f-1b2c3d4e5f60'

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  author: 'portfolio_manager',
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: [],
  tokens_in: 2200,
  tokens_out: 180,
  cost_usd: '0.004000',
  unmatched_numbers: null,
}

function pmMessage(kind: 'amend_stop' | 'close'): WarroomMessage {
  return {
    ...BASE,
    id: `m-pm-${kind}`,
    kind: 'proposal',
    content_md: kind === 'amend_stop' ? '**Move the stop to 4,117.6**, breakeven plus fees.' : '**Close all 600 contracts at market.**',
    card: {
      type: 'proposal',
      proposal_id: PM_ID,
      version: 1,
      parent_id: null,
      kind,
      author: 'portfolio_manager',
      status: 'awaiting_owner',
      inst_id: kind === 'amend_stop' ? 'XAU-USDT-SWAP' : 'TSLA-USDT-SWAP',
      side: kind === 'amend_stop' ? 'sell' : 'buy',
      order_type: null,
      entry: null,
      entry_zone: null,
      stop: kind === 'amend_stop' ? '4117.6' : null,
      take_profits: [],
      time_stop_minutes: null,
      close_pct: kind === 'close' ? '100' : null,
      leverage_suggestion: null,
      expected_r_multiple: null,
      probability_estimate: null,
      rationale_md: kind === 'amend_stop' ? 'Rule: breakeven. The short is at +1.0R.' : 'Rule: invalidation. The 1H candle closed under 440.00.',
      invalidation: null,
      evidence: [],
      created_at: '2026-10-28T11:30:00+00:00',
    },
    created_at: '2026-10-28T11:30:00.000000Z',
  }
}

function pmDetail(kind: 'amend_stop' | 'close', over: Partial<ProposalDetail> = {}): ProposalDetail {
  return {
    id: PM_ID,
    env: 'demo',
    version: 1,
    parent_id: null,
    kind,
    author: 'portfolio_manager',
    inst_id: kind === 'amend_stop' ? 'XAU-USDT-SWAP' : 'TSLA-USDT-SWAP',
    side: kind === 'amend_stop' ? 'sell' : 'buy',
    order_type: null,
    entry: null,
    entry_zone: null,
    stop: kind === 'amend_stop' ? '4117.6' : null,
    take_profits: [],
    time_stop_minutes: null,
    close_pct: kind === 'close' ? '100' : null,
    size_suggestion_pct: null,
    leverage_suggestion: null,
    expected_r: null,
    probability: null,
    rationale_md: kind === 'amend_stop' ? 'Rule: breakeven. The short is at +1.0R.' : 'Rule: invalidation. The 1H candle closed under 440.00.',
    invalidation: null,
    status: 'awaiting_owner',
    status_reason: null,
    owner_note: null,
    decided_by: null,
    decided_at: null,
    approval_expires_at: '2026-10-28T11:45:00.000000Z',
    created_at: '2026-10-28T11:30:00.000000Z',
    updated_at: '2026-10-28T11:30:00.000000Z',
    risk_check: {
      id: 'rc-pm-1',
      result: 'approved',
      decision: 'awaiting_owner',
      reason: 'OK.',
      final_contracts: kind === 'close' ? '600' : null,
      final_notional_usd: kind === 'close' ? '262830' : null,
      leverage: null,
      checks: [autonomyCheck(7, { autonomy_level: 2, max_enabled_level: 4, notional_usd: kind === 'close' ? '262830' : '163848', ask_above_usd: '3000' })],
      created_at: '2026-10-28T11:30:05.000000Z',
    },
    actions: { approve: true, approve_blocked_reason: null, edit: false, reject: true, ask_team: true },
    orders: [],
    ...over,
  }
}

function backend(state: { detail: ProposalDetail }) {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/sanctum/csrf-cookie') return new Response(null, { status: 204 })
    if (url.pathname === `/api/proposals/${PM_ID}`) return new Response(JSON.stringify({ data: state.detail }), { status: 200, headers: { 'Content-Type': 'application/json' } })
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
    const detail = pmDetail('amend_stop')
    detail.risk_check!.checks = [autonomyCheck(7, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '163848', ask_above_usd: '3000', note: 'above_ask_threshold' })]
    backend({ detail })
    draw(pmMessage('amend_stop'))
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
    const detail = pmDetail('close', { status: 'sent', decided_by: 'autonomy:3', decided_at: '2026-10-28T10:46:40.000000Z' })
    detail.risk_check!.checks = [autonomyCheck(7, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '262830', ask_above_usd: '3000', note: 'risk_reducing' })]
    backend({ detail })
    draw(pmMessage('close'))
    await waitFor(() => expect(card()).toHaveAttribute('data-status', 'sent'))
    const c = within(card())
    expect(c.getByTestId('pr-status')).toHaveTextContent('Sent without asking')
    const note = c.getByTestId('au-note')
    expect(note).toHaveTextContent('Autonomy 3 didn’t ask.')
    expect(note).toHaveTextContent('Closing never waits, whatever its size.')
    expect(c.queryByTestId('pr-actions')).toBeNull()
  })

  it('level 2 stop change: Approve and Reject show, with no autonomy note', async () => {
    const detail = pmDetail('amend_stop')
    backend({ detail })
    draw(pmMessage('amend_stop'))
    const c = within(card())
    await c.findByTestId('pr-approve')
    expect(c.getByTestId('pr-reject')).toBeInTheDocument()
    expect(c.queryByTestId('au-note')).toBeNull()
  })
})
