import { create } from 'zustand'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import type { ToolCall, WarroomMessage } from './api'

/*
 * Live agent runs (ATL-97; design v7 "Historian cards and live reasoning" §9).
 *
 * `warroom.thinking` is broadcast on private-warroom as the whole ATL-88 envelope and never stored.
 * Each run_id becomes one row: the message being written. The row closes on the `run_end` event
 * (ATL-93): `ok` means the run's final `warroom.message` replaces the row in place; `error`,
 * `rate_limited` or `degraded` stop the row with no message.
 *
 * Matching the message to its row: by `run_id` when the message carries it (the envelope does; the
 * API resource does not store it yet), otherwise by the same agent, thread and reply_to, which is
 * exact for an @mention answer (ATL-98) and for Main's scheduled runs. The runtime publishes the
 * message before `run_end`, but either order works: a row resolved by its message ignores the
 * rest of its stream.
 */

export type RunStatus = 'running' | 'ok' | 'error' | 'rate_limited' | 'degraded'
export type RunPhase = 'thinking' | 'calling' | 'writing'
export type DeltaType = 'thinking' | 'text' | 'tool_use' | 'tool_result' | 'run_end'

/** The ATL-88/ATL-93/ATL-98 thinking payload. Every key is always sent; unused ones are null. */
export interface ThinkingPayload {
  run_id: string
  agent: string
  seq: number
  delta_type: DeltaType
  thread_id?: string | null
  reply_to?: string | null
  text?: string | null
  tool?: string | null
  tool_use_id?: string | null
  /** An object, or (when `truncated`) possibly a string the bridge cut. */
  input?: unknown
  output?: string | null
  is_error?: boolean | null
  truncated?: boolean
  /** run_end only. */
  status?: Exclude<RunStatus, 'running'> | null
  error?: string | null
}

export interface ThinkingEnvelope {
  id: string
  type: 'warroom.thinking'
  ts: string
  env: TradingEnv
  source: string
  correlation_id: string | null
  payload: ThinkingPayload
}

export interface LiveTool extends ToolCall {
  pending: boolean
}

export interface LiveRun {
  id: string
  env: TradingEnv
  agent: string
  /** null: a scheduled or manual run, shown in Main. */
  threadId: string | null
  replyTo: string | null
  /** The first event's server time: the row's place in the feed. */
  startedAt: string
  /** The browser's clock when the first event arrived: the elapsed clock counts from here. */
  receivedAt: number
  endedAt: string | null
  status: RunStatus
  phase: RunPhase
  calling: string | null
  thinking: string
  text: string
  tools: LiveTool[]
  /** A thinking step arrived cut (truncated=true). */
  cut: boolean
  /** The first seq this browser saw; > 1 means it joined late. */
  joinedAt: number
  lastSeq: number
}

/** Applies one event to its run (or starts it). Pure, so the stream logic is testable on its own. */
export function applyThinking(run: LiveRun | undefined, e: ThinkingEnvelope, now: number = Date.now()): LiveRun {
  const p = e.payload
  const base: LiveRun = run ?? {
    id: p.run_id,
    env: e.env,
    agent: p.agent,
    threadId: p.thread_id ?? null,
    replyTo: p.reply_to ?? null,
    startedAt: e.ts,
    receivedAt: now,
    endedAt: null,
    status: 'running',
    phase: 'thinking',
    calling: null,
    thinking: '',
    text: '',
    tools: [],
    cut: false,
    joinedAt: p.seq,
    lastSeq: 0,
  }
  // A repeated or out-of-date event changes nothing; a stopped run takes no more deltas.
  if (run && (p.seq <= run.lastSeq || run.status !== 'running')) return run
  const next: LiveRun = { ...base, lastSeq: p.seq }
  const cut = p.truncated === true
  switch (p.delta_type) {
    case 'thinking':
      next.thinking += p.text ?? ''
      next.cut ||= cut
      next.phase = next.text ? 'writing' : 'thinking'
      next.calling = null
      break
    case 'text':
      next.text += p.text ?? ''
      next.phase = 'writing'
      next.calling = null
      break
    case 'tool_use':
      next.tools = [
        ...next.tools,
        {
          id: p.tool_use_id ?? null,
          name: p.tool ?? '',
          input: p.input ?? null,
          input_truncated: cut && typeof p.input === 'string',
          pending: true,
        },
      ]
      next.phase = 'calling'
      next.calling = p.tool ?? null
      break
    case 'tool_result': {
      let matched = false
      next.tools = next.tools.map((tc) => {
        if (matched || !tc.pending || (p.tool_use_id && tc.id !== p.tool_use_id)) return tc
        matched = true
        return { ...tc, output: p.output ?? null, output_truncated: cut, is_error: p.is_error ?? null, as_of: asOfOf(p.output), pending: false }
      })
      // A result whose call was before this browser joined still shows as a finished call.
      if (!matched) {
        next.tools = [
          ...next.tools,
          { id: p.tool_use_id ?? null, name: p.tool ?? '', input: null, output: p.output ?? null, output_truncated: cut, is_error: p.is_error ?? null, as_of: asOfOf(p.output), pending: false },
        ]
      }
      next.phase = next.text ? 'writing' : 'thinking'
      next.calling = null
      break
    }
    case 'run_end':
      next.status = p.status ?? 'error'
      next.endedAt = e.ts
      next.calling = null
      next.tools = next.tools.map((tc) => (tc.pending ? { ...tc, pending: false } : tc))
      break
  }
  return next
}

/** The Atlas tool envelope's `as_of`, when the output is (untruncated) JSON. */
function asOfOf(output: string | null | undefined): string | null {
  if (!output) return null
  try {
    const parsed: unknown = JSON.parse(output)
    if (parsed && typeof parsed === 'object' && 'as_of' in parsed && typeof parsed.as_of === 'string') return parsed.as_of
  } catch {
    // Cut or plain text: no as_of to show.
  }
  return null
}

/** Is this live run the one `m` finishes? */
export function runMatches(run: LiveRun, m: WarroomMessage, mainThreadId: string | undefined): boolean {
  if (m.run_id) return m.run_id === run.id
  if (m.author_type !== 'agent' || m.author !== run.agent) return false
  if (run.status !== 'running' && run.status !== 'ok') return false
  const thread = run.threadId ?? mainThreadId
  return thread === m.thread_id && (run.replyTo ?? null) === (m.reply_to ?? null)
}

/** Disclosure keys: a stored message's panel is its id, a live run's is `run:<id>`. */
export const runKey = (runId: string) => `run:${runId}`

/** Where focus goes once a message replaces the row the reader was on. */
export interface FocusHandoff {
  messageId: string
  part: 'toggle' | 'row'
}

interface RunsStore {
  runs: Record<string, LiveRun>
  /** Runs already answered or dismissed: their late events are dropped. */
  closed: Record<string, true>
  /** Open disclosures: panel keys (message id / run:<id>) and tool keys (`<panel>:<index>`). */
  open: Record<string, boolean>
  focus: FocusHandoff | null
  apply: (e: ThinkingEnvelope) => { run: LiveRun; started: boolean } | null
  /** Removes the run `m` answers and hands its open panels to the message. Returns that run. */
  resolve: (m: WarroomMessage, mainThreadId: string | undefined, focusOn?: 'toggle' | 'row' | null) => LiveRun | null
  dismiss: (runId: string) => void
  setOpen: (key: string, open: boolean) => void
  takeFocus: (messageId: string) => FocusHandoff | null
  reset: () => void
}

export const useRuns = create<RunsStore>((set, get) => ({
  runs: {},
  closed: {},
  open: {},
  focus: null,
  apply: (e) => {
    const id = e.payload?.run_id
    if (!id || get().closed[id]) return null
    const prev = get().runs[id]
    const run = applyThinking(prev, e)
    if (run !== prev) set((s) => ({ runs: { ...s.runs, [id]: run } }))
    return { run, started: !prev }
  },
  resolve: (m, mainThreadId, focusOn = null) => {
    const candidates = Object.values(get().runs)
      .filter((r) => runMatches(r, m, mainThreadId))
      .sort((a, b) => (a.startedAt < b.startedAt ? -1 : a.startedAt > b.startedAt ? 1 : 0))
    const run = candidates[0]
    if (!run) return null
    set((s) => {
      const runs = { ...s.runs }
      delete runs[run.id]
      const open = { ...s.open }
      const prefix = runKey(run.id)
      for (const [key, value] of Object.entries(s.open)) {
        if (!value || (key !== prefix && !key.startsWith(`${prefix}:`))) continue
        open[m.id + key.slice(prefix.length)] = true
        delete open[key]
      }
      return { runs, open, closed: { ...s.closed, [run.id]: true }, focus: focusOn ? { messageId: m.id, part: focusOn } : s.focus }
    })
    return run
  },
  dismiss: (runId) =>
    set((s) => {
      if (!s.runs[runId]) return s
      const runs = { ...s.runs }
      delete runs[runId]
      return { runs, closed: { ...s.closed, [runId]: true } }
    }),
  setOpen: (key, open) => set((s) => ({ open: { ...s.open, [key]: open } })),
  takeFocus: (messageId) => {
    const f = get().focus
    if (!f || f.messageId !== messageId) return null
    set({ focus: null })
    return f
  },
  reset: () => set({ runs: {}, closed: {}, focus: null }),
}))

/** The live runs drawn in `thread`, oldest first. */
export function runsForThread(runs: Record<string, LiveRun>, env: TradingEnv, threadId: string, isMain: boolean): LiveRun[] {
  return Object.values(runs)
    .filter((r) => r.env === env && (r.threadId === threadId || (r.threadId === null && isMain)))
    .sort((a, b) => (a.startedAt < b.startedAt ? -1 : a.startedAt > b.startedAt ? 1 : 0))
}

/** "mcp__history__find_analogs" → "history.find_analogs"; built-ins as given. */
export function toolLabel(name: string): string {
  const parts = name.split('__')
  return parts[0] === 'mcp' && parts.length >= 3 ? `${parts[1]}.${parts.slice(2).join('__')}` : name
}

/** The first two scalar input values: "BTC-USDT-SWAP, 1D". */
export function argSummary(input: unknown): string {
  if (input === null || input === undefined) return ''
  if (typeof input === 'string') return input.slice(0, 80)
  if (typeof input !== 'object') return String(input)
  const values: string[] = []
  for (const v of Object.values(input as Record<string, unknown>)) {
    const x: unknown = Array.isArray(v) ? v[0] : v
    if (x !== null && x !== undefined && typeof x !== 'object') values.push(String(x))
    if (values.length === 2) break
  }
  return values.join(', ')
}

/** The latest sentence of the thinking so far. */
export function lastSentence(text: string): string {
  const lines = text.trim().split(/\n+/)
  const line = lines[lines.length - 1] ?? ''
  const sentences = line.split(/(?<=[.!?؟])\s+/).filter(Boolean)
  return sentences[sentences.length - 1] ?? ''
}
