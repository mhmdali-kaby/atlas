import { ChevronRight, CircleX, Info, Scissors } from 'lucide-react'
import { Collapsible } from 'radix-ui'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { Spinner } from '../../ui'
import type { ToolCall, WarroomMessage } from './api'
import { argSummary, toolLabel, useRuns, type LiveTool } from './runs'
import { formatFullTime, formatTime } from './time'

/*
 * "Show reasoning" (brief §12; design v7 §8). A stored agent message reads its reasoning_md and
 * tool_calls (ATL-88 final-message shape); a live run (LiveRunRow) renders the same panel from its
 * warroom.thinking events, so the panel looks the same mid-run and after the answer lands.
 * Collapsed by default; Radix Collapsible gives the button its aria-expanded/aria-controls and
 * keyboard operation. Open state lives in useRuns, so it survives the virtualised feed unmounting a
 * row and carries over from a live row to its message.
 */

/** Past this many lines a JSON block is trimmed, unless only a line or two would be hidden. */
const IO_LINES = 8

/** A figure kept left-to-right inside Arabic text. */
function Fig({ children }: { children: ReactNode }) {
  return (
    <bdi dir="ltr" className="num">
      {children}
    </bdi>
  )
}

function parseJson(text: string | null | undefined): unknown {
  if (!text) return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return null
  }
}

type Result = { tone: '' | 'is-err' | 'is-warn' | 'is-run'; text: ReactNode }

function useResult() {
  const { t } = useTranslation('warroom')
  return (tc: ToolCall & { pending?: boolean }): Result => {
    if (tc.pending) return { tone: 'is-run', text: t('reason.res.run') }
    if (tc.is_error) return { tone: 'is-err', text: t('reason.res.err') }
    const parsed = parseJson(tc.output) as Record<string, unknown> | null
    const data = (parsed && typeof parsed === 'object' ? ((parsed.data as Record<string, unknown> | undefined) ?? parsed) : null) as
      | Record<string, unknown>
      | null
    const sample = data?.sample as { n?: unknown } | undefined
    const n = typeof sample?.n === 'number' ? sample.n : undefined
    if (data?.status === 'no_data') return { tone: '', text: t('reason.res.none') }
    if (data?.status === 'insufficient_data' && n !== undefined) return { tone: 'is-warn', text: t('reason.res.few', { n }) }
    if (n !== undefined) return { tone: '', text: <Fig>n = {n}</Fig> }
    return { tone: '', text: t('reason.res.ok') }
  }
}

/** Pretty JSON, keys in secondary ink; a string that is not JSON is shown as it came. */
function prettyLines(value: unknown): string[] {
  let text: string
  if (typeof value === 'string') {
    const parsed = parseJson(value)
    text = parsed !== null ? JSON.stringify(parsed, null, 2) : value
  } else {
    text = JSON.stringify(value ?? null, null, 2)
  }
  return text.split('\n')
}

const KEY_LINE = /^(\s*)("(?:[^"\\]|\\.)*")(:)(.*)$/

function JsonLine({ line }: { line: string }) {
  const m = KEY_LINE.exec(line)
  if (!m) return <>{line}</>
  return (
    <>
      {m[1]}
      <span className="j-k">{m[2]}</span>
      {m[3]}
      {m[4]}
    </>
  )
}

function IoBlock({ id, label, value, cut, cutMsg, asOf, lang }: { id: string; label: string; value: unknown; cut: boolean; cutMsg: string; asOf?: string | null; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const [all, setAll] = useState(false)
  const lines = prettyLines(value)
  const long = lines.length > IO_LINES + 2
  const shown = long && !all ? [...lines.slice(0, IO_LINES), '…'] : lines
  return (
    <div className="io">
      <p className="io-head">
        <span className="io-label">{label}</span>
        {asOf && (
          <span className="io-asof" title={formatFullTime(asOf, lang)}>
            {t('reason.asOf', { time: formatTime(asOf, lang) })}
          </span>
        )}
      </p>
      <pre className="io-pre" dir="ltr" id={id}>
        {shown.map((line, i) => (
          <span key={i}>
            <JsonLine line={line} />
            {i < shown.length - 1 ? '\n' : ''}
          </span>
        ))}
      </pre>
      {cut && (
        <p className="io-cut">
          <Scissors aria-hidden="true" strokeWidth={1.5} />
          <span>{cutMsg}</span>
        </p>
      )}
      {long && (
        <button type="button" className="io-more" aria-expanded={all} aria-controls={id} onClick={() => setAll((v) => !v)}>
          {all ? t('reason.less') : t('reason.lines', { count: lines.length })}
        </button>
      )}
    </div>
  )
}

function ToolRow({ k, tc, lang }: { k: string; tc: ToolCall & { pending?: boolean }; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const result = useResult()
  const pending = Boolean(tc.pending)
  const isOpen = useRuns((s) => Boolean(s.open[k])) && !pending
  const setOpen = useRuns((s) => s.setOpen)
  const r = result(tc)
  const domId = `tb-${k.replace(/[^\w-]/g, '_')}`
  return (
    <Collapsible.Root asChild open={isOpen} onOpenChange={(o) => setOpen(k, o)} disabled={pending}>
      <li className={['tool', tc.is_error ? 'is-error' : '', pending ? 'is-pending' : ''].filter(Boolean).join(' ')} data-testid="wr-tool">
        <Collapsible.Trigger className="tool-head" aria-controls={domId}>
          <ChevronRight aria-hidden="true" strokeWidth={1.5} className="tool-chev" />
          <span className="tool-name">{toolLabel(tc.name)}</span>
          <span className="tool-args" dir="ltr">
            {argSummary(tc.input)}
          </span>
          <span className={`tool-res ${r.tone}`}>
            {pending ? <Spinner className="tool-spin" /> : tc.is_error ? <CircleX aria-hidden="true" strokeWidth={1.5} /> : null}
            <span>{r.text}</span>
          </span>
        </Collapsible.Trigger>
        <Collapsible.Content className="tool-body" id={domId}>
          <IoBlock id={`${domId}-in`} label={t('reason.input')} value={tc.input} cut={Boolean(tc.input_truncated)} cutMsg={t('reason.cutIn')} lang={lang} />
          <IoBlock
            id={`${domId}-out`}
            label={t('reason.output')}
            value={tc.output ?? null}
            cut={Boolean(tc.output_truncated)}
            cutMsg={t('reason.cutOut')}
            asOf={tc.as_of}
            lang={lang}
          />
        </Collapsible.Content>
      </li>
    </Collapsible.Root>
  )
}

export interface PanelLive {
  /** Draw the caret after the reasoning text (the run is thinking). */
  caret: boolean
  /** A thinking step arrived truncated. */
  cut: boolean
  /** The first seq this browser saw. */
  joinedAt: number
}

/** The panel's contents: reasoning, tool calls, then the foot (the cost line). */
function PanelBody({ panelKey, reasoning, tools, live, foot, lang }: { panelKey: string; reasoning: string; tools: Array<ToolCall | LiveTool>; live?: PanelLive; foot?: ReactNode; lang: Lang }) {
  const { t } = useTranslation('warroom')
  return (
    <>
      {live && live.joinedAt > 1 && (
        <p className="run-joined">
          <Info aria-hidden="true" strokeWidth={1.5} />
          <span>{t('run.joined', { n: live.joinedAt })}</span>
        </p>
      )}
      {reasoning ? (
        <p className={live?.caret ? 'reason-text has-caret' : 'reason-text'} dir="auto" data-testid="wr-reason-text">
          {reasoning}
        </p>
      ) : live ? null : (
        <p className="reason-text">{t('reason.none')}</p>
      )}
      {live?.cut && (
        <p className="io-cut" data-testid="wr-reason-cut">
          <Scissors aria-hidden="true" strokeWidth={1.5} />
          <span>{t('reason.cutText')}</span>
        </p>
      )}
      {tools.length > 0 && (
        <ol className="tool-calls">
          {tools.map((tc, i) => (
            <ToolRow key={i} k={`${panelKey}:${i}`} tc={tc} lang={lang} />
          ))}
        </ol>
      )}
      {foot}
    </>
  )
}

export interface ReasonDisclosureProps {
  panelKey: string
  reasoning: string
  tools: Array<ToolCall | LiveTool>
  /** The toggle's words when closed / open. */
  showLabel: string
  hideLabel: string
  live?: PanelLive
  foot?: ReactNode
  lang: Lang
}

/** The disclosure button and its panel. */
export function ReasonDisclosure({ panelKey, reasoning, tools, showLabel, hideLabel, live, foot, lang }: ReasonDisclosureProps) {
  const { t } = useTranslation('warroom')
  const open = useRuns((s) => Boolean(s.open[panelKey]))
  const setOpen = useRuns((s) => s.setOpen)
  const panelId = `rs-${panelKey.replace(/[^\w-]/g, '_')}`
  return (
    <Collapsible.Root className="reason" open={open} onOpenChange={(o) => setOpen(panelKey, o)}>
      <Collapsible.Trigger className="disclosure-btn reason-btn" aria-controls={panelId} data-testid="wr-reason-toggle" data-reason-key={panelKey}>
        <ChevronRight aria-hidden="true" strokeWidth={1.5} className="disc-chev" />
        <span>{open ? hideLabel : showLabel}</span>
        {tools.length > 0 && <span className="reason-count">{t('reason.calls', { count: tools.length })}</span>}
      </Collapsible.Trigger>
      <Collapsible.Content className={live ? 'reason-panel is-live' : 'reason-panel'} id={panelId} data-testid="wr-reason-panel">
        <PanelBody panelKey={panelKey} reasoning={reasoning} tools={tools} live={live} foot={foot} lang={lang} />
      </Collapsible.Content>
    </Collapsible.Root>
  )
}

function CostLine({ m, lang }: { m: WarroomMessage; lang: Lang }) {
  const { t } = useTranslation('warroom')
  if (m.tokens_in === null || m.tokens_in === undefined) return null
  return (
    <p className="reason-cost" tabIndex={0} title={t('reason.costTip')} aria-description={t('reason.costTip')}>
      {t('reason.cost', {
        tin: formatFixed(m.tokens_in, 0, lang),
        tout: formatFixed(m.tokens_out ?? 0, 0, lang),
        cost: m.cost_usd ? `$${formatFixed(m.cost_usd, 3, lang)}` : '—',
      })}
    </p>
  )
}

/** "Show reasoning" on a stored agent message; nothing when it has neither reasoning nor tools. */
export function MessageReasoning({ message: m, lang }: { message: WarroomMessage; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const tools = Array.isArray(m.tool_calls) ? m.tool_calls.filter((tc): tc is ToolCall => Boolean(tc && typeof tc === 'object' && 'name' in tc)) : []
  if (!m.reasoning_md && tools.length === 0) return null
  return (
    <ReasonDisclosure
      panelKey={m.id}
      reasoning={m.reasoning_md ?? ''}
      tools={tools}
      showLabel={t('reason.show')}
      hideLabel={t('reason.hide')}
      foot={<CostLine m={m} lang={lang} />}
      lang={lang}
    />
  )
}
