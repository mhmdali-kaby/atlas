import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import type { WarroomMessage, WarroomThread } from './api'
import { MessageReasoning } from './Reasoning'
import warroom from './routes'
import { applyThinking, argSummary, lastSentence, toolLabel, useRuns, type ThinkingEnvelope, type ThinkingPayload } from './runs'

/* A socket double: the test calls the handlers the War Room registers on private-warroom. */
const handlers = new Map<string, (data: unknown) => void>()
vi.mock('../../lib/echo', () => ({
  getEcho: () => ({
    private: () => ({
      listen: (event: string, fn: (data: unknown) => void) => handlers.set(event, fn),
      stopListening: (event: string) => handlers.delete(event),
    }),
  }),
  hasConnected: () => true,
  reconnectEcho: () => {},
  useLiveStatus: () => 'connected',
}))

const NOW = Date.parse('2026-10-30T09:12:10Z')
const MAIN: WarroomThread = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-30T09:12:00.000000Z', created_at: '2026-07-14T15:02:00.000000Z' }

const QUESTION: WarroomMessage = {
  id: 'q-1',
  thread_id: 'main-1',
  author_type: 'human',
  author: 'Mhmd',
  kind: 'text',
  content_md: '@Historian what usually happens to BTC after a 3-day 10% drop?',
  card: null,
  reply_to: null,
  mentions: ['historian'],
  reasoning_md: null,
  tool_calls: null,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  created_at: '2026-10-30T09:12:00.000000Z',
}

/* The ATL-88 final-message shape: reasoning, two tool calls, tokens and notional cost. */
const ANSWER: WarroomMessage = {
  ...QUESTION,
  id: 'a-1',
  author_type: 'agent',
  author: 'historian',
  kind: 'text',
  content_md: 'History weakly supports a bounce: 12 cases.',
  reply_to: 'q-1',
  mentions: [],
  reasoning_md: 'Regime first.\n\nThen analogs on the hourly bars.',
  tool_calls: [
    {
      id: 'toolu_1',
      name: 'mcp__history__detect_regime',
      input: { inst_id: 'BTC-USDT-SWAP', bar: '1D' },
      input_truncated: false,
      as_of: '2026-10-30T09:00:00+00:00',
      output: '{"as_of":"2026-10-30T09:00:00+00:00","source":"history","data":{"status":"ok","sample":{"n":41}}}',
      output_truncated: false,
      is_error: null,
    },
    {
      id: 'toolu_2',
      name: 'mcp__history__find_analogs',
      input: { inst_id: 'BTC-USDT-SWAP', lookback_years: 3 },
      input_truncated: false,
      as_of: null,
      output: 'boom',
      output_truncated: false,
      is_error: true,
    },
  ],
  tokens_in: 2984,
  tokens_out: 412,
  cost_usd: '0.018000',
  created_at: '2026-10-30T09:12:09.000000Z',
}

function envelope(seq: number, payload: Partial<ThinkingPayload>, ts = '2026-10-30T09:12:01Z'): ThinkingEnvelope {
  return {
    id: `e-${seq}`,
    type: 'warroom.thinking',
    ts,
    env: 'demo',
    source: 'agent:historian',
    correlation_id: 'session-1',
    payload: {
      run_id: 'run-1',
      agent: 'historian',
      seq,
      delta_type: 'thinking',
      thread_id: 'main-1',
      reply_to: 'q-1',
      text: null,
      tool: null,
      tool_use_id: null,
      input: null,
      output: null,
      is_error: null,
      truncated: false,
      ...payload,
    },
  }
}

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function fakeBackend(messages: WarroomMessage[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input))
      if (url.pathname === '/api/warroom/threads') return reply(200, { data: [MAIN] })
      if (url.pathname === '/api/warroom/messages') return reply(200, { data: [...messages].reverse(), meta: { next_cursor: null } })
      return reply(404, { message: 'Not found' })
    }),
  )
}

function renderWarRoom() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    warroom.routes.map((r) => ({ ...r, path: `/${r.path}`, element: <>{r.element}</> })),
    { initialEntries: ['/warroom'] },
  )
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

/** The one stored answer in the feed (its figures are LTR islands, so match on the article). */
function answerArticle(feed: HTMLElement): HTMLElement {
  const nodes = feed.querySelectorAll<HTMLElement>('[data-msg-id="a-1"]')
  expect(nodes).toHaveLength(1)
  return nodes[0]!
}

const occurrences = (el: HTMLElement, text: string) => (el.textContent ?? '').split(text).length - 1

const emit = (event: string, data: unknown) =>
  act(() => {
    const fn = handlers.get(event)
    if (!fn) throw new Error(`nothing listens to ${event}`)
    fn(data)
  })

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
  useRuns.setState({ runs: {}, closed: {}, open: {}, focus: null })
  // jsdom has no layout: give the virtualised feed a viewport and its rows a height.
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(800)
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(720)
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  handlers.clear()
})

describe('Show reasoning on a stored agent message', () => {
  it('is collapsed by default; Enter shows the reasoning and both tool calls with their inputs', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(<MessageReasoning message={ANSWER} lang="en" />)

    const toggle = screen.getByRole('button', { name: /Show reasoning/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent('2 tool calls')
    expect(screen.queryByText(/Regime first/)).not.toBeInTheDocument()
    expect(screen.queryByText('history.detect_regime')).not.toBeInTheDocument()

    await user.tab()
    expect(toggle).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveTextContent('Hide reasoning')
    const panel = screen.getByTestId('wr-reason-panel')
    expect(within(panel).getByTestId('wr-reason-text')).toHaveTextContent('Regime first. Then analogs on the hourly bars.')
    const tools = within(panel).getAllByTestId('wr-tool')
    expect(tools).toHaveLength(2)
    expect(tools[0]).toHaveTextContent('history.detect_regime')
    expect(tools[0]).toHaveTextContent('BTC-USDT-SWAP, 1D')
    expect(tools[0]).toHaveTextContent('n = 41')
    expect(tools[1]).toHaveTextContent('history.find_analogs')
    expect(tools[1]).toHaveTextContent('BTC-USDT-SWAP, 3')
    expect(tools[1]).toHaveTextContent('Failed')
    expect(within(panel).getByText('2,984 tokens in, 412 out, about $0.018 at API prices')).toBeInTheDocument()

    // Each call is its own disclosure: Enter on it shows the input and output JSON.
    const callButton = within(tools[0]!).getByRole('button')
    expect(callButton).toHaveAttribute('aria-expanded', 'false')
    callButton.focus()
    await user.keyboard('{Enter}')
    expect(callButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(tools[0]!).getByText('Input')).toBeInTheDocument()
    expect(within(tools[0]!).getByText('Output')).toBeInTheDocument()
    expect(within(tools[0]!).getByText('"inst_id"')).toBeInTheDocument()

    // Enter again closes the panel.
    toggle.focus()
    await user.keyboard('{Enter}')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByTestId('wr-reason-panel')).not.toBeVisible()
    expect(screen.queryByText('history.detect_regime')).not.toBeInTheDocument()
  })

  it('trims JSON past 8 lines with "Show all N lines" and marks a server cut', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const long = Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`k${i}`, i]))
    const m: WarroomMessage = {
      ...ANSWER,
      reasoning_md: null,
      tool_calls: [{ name: 'mcp__okx_market__get_candles', input: long, output: '{"as_of":', output_truncated: true }],
    }
    render(<MessageReasoning message={m} lang="en" />)
    await user.click(screen.getByRole('button', { name: /Show reasoning/ }))
    await user.click(within(screen.getByTestId('wr-tool')).getByRole('button'))
    const more = screen.getByRole('button', { name: 'Show all 14 lines' })
    expect(screen.queryByText('"k11"')).not.toBeInTheDocument()
    await user.click(more)
    expect(screen.getByText('"k11"')).toBeInTheDocument()
    expect(screen.getByText('Atlas cut this output at 2,000 characters.')).toBeInTheDocument()
  })

  it('draws nothing for a message with no reasoning and no tool calls', () => {
    const { container } = render(<MessageReasoning message={{ ...ANSWER, reasoning_md: null, tool_calls: null }} lang="en" />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('the live run row', () => {
  it('streams three thinking events, then the final message replaces the row with no duplicate', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    fakeBackend([QUESTION])
    renderWarRoom()
    const feed = await screen.findByTestId('wr-feed')
    await within(feed).findByText(/what usually happens to BTC/)

    emit('.warroom.thinking', envelope(1, { delta_type: 'thinking', text: 'Regime first. ' }))
    emit('.warroom.thinking', envelope(2, { delta_type: 'tool_use', tool: 'mcp__history__detect_regime', tool_use_id: 'toolu_1', input: { inst_id: 'BTC-USDT-SWAP', bar: '1D' } }))
    emit('.warroom.thinking', envelope(3, { delta_type: 'text', text: 'History weakly supports a bounce' }))

    const row = await within(feed).findByTestId('wr-run')
    expect(row).toHaveAttribute('aria-busy', 'true')
    expect(row).toHaveTextContent('Historian')
    expect(row).toHaveTextContent('Writing')
    expect(row).toHaveTextContent('History weakly supports a bounce')
    // Under the question it answers.
    expect(within(row).getByRole('link', { name: /Show the message/ })).toBeInTheDocument()
    // Announced politely: the start, the tool call, the writing; never the streamed text.
    expect(screen.getByTestId('wr-live')).toHaveTextContent('Historian is writing an answer.')

    // The live panel shows what streamed so far, with the call still running.
    await user.click(within(row).getByRole('button', { name: /Show live reasoning/ }))
    expect(within(row).getByTestId('wr-reason-text')).toHaveTextContent('Regime first.')
    expect(within(row).getByTestId('wr-tool')).toHaveTextContent('Running')

    // The runtime publishes the message, then run_end ok (ATL-93), both with the same run_id.
    emit('.warroom.message', { ...ANSWER, run_id: 'run-1' })
    emit('.warroom.thinking', envelope(4, { delta_type: 'run_end', status: 'ok', error: null }))

    await waitFor(() => expect(within(feed).queryByTestId('wr-run')).not.toBeInTheDocument())
    const article = answerArticle(feed)
    expect(article).toHaveTextContent('History weakly supports a bounce: 12 cases.')
    // The open live panel carries over as the message's open disclosure, read from the stored record.
    const toggle = within(article as HTMLElement).getByRole('button', { name: /Hide reasoning/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(within(article as HTMLElement).getAllByTestId('wr-tool')).toHaveLength(2)
    expect(occurrences(feed, 'History weakly supports a bounce')).toBe(1)
  })

  it('without a run_id on the message, the same agent, thread and reply_to finish the row', async () => {
    fakeBackend([QUESTION])
    renderWarRoom()
    const feed = await screen.findByTestId('wr-feed')
    await within(feed).findByText(/what usually happens to BTC/)
    emit('.warroom.thinking', envelope(1, { text: 'Regime first.' }))
    emit('.warroom.thinking', envelope(2, { text: ' Then analogs.' }))
    emit('.warroom.thinking', envelope(3, { delta_type: 'text', text: 'History weakly' }))
    await within(feed).findByTestId('wr-run')

    emit('.warroom.message', ANSWER)
    await waitFor(() => expect(within(feed).queryByTestId('wr-run')).not.toBeInTheDocument())
    expect(answerArticle(feed)).toHaveTextContent('History weakly supports a bounce: 12 cases.')
    expect(occurrences(feed, 'History weakly supports a bounce')).toBe(1)
    // A late event of a finished run does not bring the row back.
    emit('.warroom.thinking', envelope(4, { delta_type: 'run_end', status: 'ok' }))
    expect(within(feed).queryByTestId('wr-run')).not.toBeInTheDocument()
  })

  it('a truncated thinking event shows the translated "truncated" note, in Arabic too', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    fakeBackend([QUESTION])
    renderWarRoom()
    const feed = await screen.findByTestId('wr-feed')
    await within(feed).findByText(/what usually happens to BTC/)
    emit('.warroom.thinking', envelope(1, { text: 'A very long step', truncated: true }))
    const row = await within(feed).findByTestId('wr-run')
    await user.click(within(row).getByRole('button', { name: /Show live reasoning/ }))
    expect(within(row).getByTestId('wr-reason-cut')).toHaveTextContent(
      'Atlas showed the first 4,000 characters of this step. The full reasoning comes with the answer.',
    )
    await act(() => i18n.changeLanguage('ar'))
    expect(within(row).getByTestId('wr-reason-cut')).toHaveTextContent('عرض أطلس أول 4,000 حرف من هذه الخطوة. يصل التفكير كاملًا مع الإجابة.')
  })

  it('a rate-limited run stops, says why and keeps what it streamed', async () => {
    fakeBackend([QUESTION])
    renderWarRoom()
    const feed = await screen.findByTestId('wr-feed')
    await within(feed).findByText(/what usually happens to BTC/)
    emit('.warroom.thinking', envelope(5, { text: 'Regime first.' }))
    emit('.warroom.thinking', envelope(6, { delta_type: 'run_end', status: 'rate_limited', error: 'rate_limited' }, '2026-10-30T09:12:05Z'))
    const row = await within(feed).findByTestId('wr-run')
    expect(row).not.toHaveAttribute('aria-busy')
    expect(row).toHaveAttribute('data-status', 'rate_limited')
    expect(row).toHaveTextContent('Stopped: usage limit')
    expect(row).toHaveTextContent('Your Claude plan’s usage limit was reached')
    expect(within(row).getByRole('button', { name: /Show reasoning so far/ })).toBeInTheDocument()
    expect(screen.getByTestId('wr-live')).toHaveTextContent('Historian stopped.')
  })
})

describe('run stream helpers', () => {
  it('orders by seq, joins late, and ignores repeats', () => {
    let run = applyThinking(undefined, envelope(9, { text: 'a' }), 0)
    expect(run.joinedAt).toBe(9)
    run = applyThinking(run, envelope(9, { text: 'dup' }), 0)
    expect(run.thinking).toBe('a')
    run = applyThinking(run, envelope(10, { delta_type: 'tool_use', tool: 'mcp__history__seasonality', tool_use_id: 't', input: 'cut {"a"', truncated: true }), 0)
    expect(run.phase).toBe('calling')
    expect(run.tools[0]).toMatchObject({ pending: true, input_truncated: true })
    run = applyThinking(run, envelope(11, { delta_type: 'tool_result', tool: 'mcp__history__seasonality', tool_use_id: 't', output: '{"as_of":"2026-10-30T03:00:00Z"}' }), 0)
    expect(run.tools[0]).toMatchObject({ pending: false, as_of: '2026-10-30T03:00:00Z' })
    expect(run.phase).toBe('thinking')
  })

  it('labels tools, summarises inputs and finds the last sentence', () => {
    expect(toolLabel('mcp__history__find_analogs')).toBe('history.find_analogs')
    expect(toolLabel('WebSearch')).toBe('WebSearch')
    expect(argSummary({ inst_id: 'XAU-USDT-SWAP', windows: ['24h'], nested: { a: 1 }, bar: '1H' })).toBe('XAU-USDT-SWAP, 24h')
    expect(lastSentence('First. Second one!\nThird line. Last bit')).toBe('Last bit')
  })
})
