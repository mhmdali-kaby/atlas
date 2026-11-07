import { CloudOff, RefreshCw, UsersRound } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatMoney } from '../../lib/format'
import { Button, Skeleton } from '../../ui'
import { formatClock } from '../portfolio/time'
import { useLang, useNow } from '../portfolio/hooks'
import { AgentRowView, useAgentName } from './AgentRow'
import { useAgents, useAgentStateEvents, type AgentRow } from './api'
import { ClaudePanel } from './ClaudePanel'
import { compact, shownState, sumNotional, type ShownState } from './model'
import { Fig, NS, Tip, Tr } from './parts'
import '../settings-connections/settings.css'
import '../warroom/warroom.css'
import './agents.css'

/*
 * Agents (ATL-117; brief §12 sidebar, §14.7; decision 242; design v9 "Agents page"). The team's
 * health at a glance: every agent's state in words, today's usage against its daily budget, model
 * and prompt version, the last run, and the Claude CLI check that gates them all. Reads
 * GET /api/agents and GET /api/agents/claude-status (ATL-113) and refetches the list on every
 * system.agent_state event. No enable/disable here: that is a versioned setting (Settings → Agents).
 */

function Head({ updatedAt }: { updatedAt: number | null }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  return (
    <header className="ag-head">
      <div className="ag-head-text">
        <h2 className="text-title-1 font-semibold">{t('title')}</h2>
        <p className="text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
      {updatedAt !== null && (
        <p className="ag-updated" data-testid="ag-updated">
          <Tr k="updated" values={{ time: formatClock(updatedAt, lang, true) }} />
        </p>
      )}
    </header>
  )
}

function TeamHead() {
  const { t } = useTranslation(NS)
  return (
    <div className="ag-row ag-row-head" role="row">
      {(['agent', 'status', 'today', 'model', 'run'] as const).map((c) => (
        <div key={c} className={`ag-c ag-c-${c}`} role="columnheader">
          {t(`col.${c}`)}
        </div>
      ))}
    </div>
  )
}

/** The notional cost in the totals line, with the "your Claude plan" tooltip (Trans fills its children). */
function Notional({ children }: { children?: ReactNode }) {
  const { t } = useTranslation(NS)
  return (
    <Tip label={t('costTip')}>
      <span className="ag-notional" tabIndex={0}>
        <Fig>{children}</Fig>
      </span>
    </Tip>
  )
}

function Totals({ rows }: { rows: AgentRow[] }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const on = rows.filter((d) => d.today.runs > 0)
  if (!on.length) {
    return (
      <p className="ag-totals" data-testid="ag-totals">
        {t('totalsNone')}
      </p>
    )
  }
  const tokens = on.reduce((s, d) => s + d.today.tokens, 0)
  const turns = on.reduce((s, d) => s + d.today.turns, 0)
  const cost = formatMoney(sumNotional(on.map((d) => d.today.cost_usd_notional)), 'USD', lang)
  return (
    <p className="ag-totals" data-testid="ag-totals">
      <Tr
        k="totals"
        values={{ tokens: compact(tokens, lang), turns: String(turns), agents: t('agentCount', { count: on.length }), cost }}
        components={{ c: <Notional /> }}
      />
    </p>
  )
}

function Loading() {
  const { t } = useTranslation(NS)
  return (
    <div className="ag-page" aria-busy="true" aria-label={t('loading')} data-testid="ag-loading">
      <Head updatedAt={null} />
      <section className="ag-group ag-claude">
        <div className="ag-cl-head">
          <Skeleton style={{ width: 36, height: 36, borderRadius: 10 }} />
          <span className="grid gap-2">
            <Skeleton style={{ width: 240, height: 15, maxWidth: '100%' }} />
            <Skeleton style={{ width: 360, height: 12, maxWidth: '100%' }} />
          </span>
        </div>
      </section>
      <Skeleton style={{ width: 420, height: 13, maxWidth: '100%' }} />
      <section className="ag-group ag-team">
        <TeamHead />
        {[110, 92, 128, 76, 120, 140, 84, 116, 132].map((w, i) => (
          <div className="ag-row is-skel" aria-hidden="true" key={i}>
            <div className="ag-c ag-c-agent">
              <Skeleton style={{ width: 36, height: 36, borderRadius: '50%' }} />
              <span className="grid gap-1.5">
                <Skeleton style={{ width: w, height: 13 }} />
                <Skeleton style={{ width: w + 50, height: 11 }} />
              </span>
            </div>
            <div className="ag-c ag-c-status">
              <Skeleton style={{ width: 110, height: 13 }} />
              <Skeleton style={{ width: w + 60, height: 11, marginTop: 6, maxWidth: '100%' }} />
            </div>
            <div className="ag-c ag-c-today">
              <Skeleton style={{ width: 150, height: 11 }} />
              <Skeleton style={{ width: '100%', height: 4, marginTop: 6 }} />
              <Skeleton style={{ width: 120, height: 11, marginTop: 10 }} />
              <Skeleton style={{ width: '100%', height: 4, marginTop: 6 }} />
            </div>
            <div className="ag-c ag-c-model">
              <Skeleton style={{ width: 90, height: 13 }} />
              <Skeleton style={{ width: 130, height: 11, marginTop: 6, maxWidth: '100%' }} />
            </div>
            <div className="ag-c ag-c-run">
              <Skeleton style={{ width: 70, height: 13 }} />
              <Skeleton style={{ width: 110, height: 11, marginTop: 6, maxWidth: '100%' }} />
            </div>
          </div>
        ))}
      </section>
    </div>
  )
}

function PageState({ kind, onRetry, retrying }: { kind: 'error' | 'empty'; onRetry: () => void; retrying: boolean }) {
  const { t } = useTranslation(NS)
  const key = kind === 'error' ? 'err' : 'empty'
  return (
    <section className="ag-group st-empty" role={kind === 'error' ? 'alert' : undefined} data-testid={`ag-${kind}`}>
      <span className={kind === 'error' ? 'st-empty-icon is-error' : 'st-empty-icon'} aria-hidden="true">
        {kind === 'error' ? <CloudOff strokeWidth={1.5} /> : <UsersRound strokeWidth={1.5} />}
      </span>
      <h2 className="text-title-3">{t(`${key}.title`)}</h2>
      <p className="max-w-[52ch] text-subheadline text-label-secondary">
        <Tr k={`${key}.body`} components={{ cmd: <code className="cmd" dir="ltr" /> }} />
      </p>
      <Button variant="secondary" icon={<RefreshCw />} className="mt-2" onClick={onRetry} loading={retrying}>
        {t('retry')}
      </Button>
    </section>
  )
}

/**
 * Which rows changed state since the last data, so their status cell flashes once and the change
 * is announced politely (design: "the changed status flashes once").
 */
function useStateChanges(rows: AgentRow[] | undefined) {
  const { t } = useTranslation(NS)
  const names = useAgentName()
  const prev = useRef<Map<string, ShownState> | null>(null)
  const [flash, setFlash] = useState<{ slugs: Set<string>; n: number }>({ slugs: new Set(), n: 0 })
  const [announce, setAnnounce] = useState('')
  useEffect(() => {
    if (!rows) return
    const next = new Map(rows.map((d) => [d.slug, shownState(d)] as const))
    const before = prev.current
    prev.current = next
    if (!before) return
    const changed = rows.filter((d) => before.has(d.slug) && before.get(d.slug) !== next.get(d.slug))
    if (!changed.length) return
    setFlash((f) => ({ slugs: new Set(changed.map((d) => d.slug)), n: f.n + 1 }))
    const last = changed[changed.length - 1]!
    setAnnounce(t('rowSr', { name: names.name(last), state: t(`state.${next.get(last.slug)}`) }))
  }, [rows, t, names])
  return { flash, announce }
}

export function AgentsPage() {
  const env = useTradingEnv()
  const q = useAgents(env)
  useAgentStateEvents(env)
  const now = useNow(30_000)
  const rows = q.data?.data
  const { flash, announce } = useStateChanges(rows)

  if (q.isPending) return <Loading />
  if (q.isError || !rows) {
    return (
      <div className="ag-page">
        <Head updatedAt={null} />
        <PageState kind="error" onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </div>
    )
  }

  return (
    <Tooltip.Provider delayDuration={450}>
      <div className="ag-page">
        <Head updatedAt={q.dataUpdatedAt} />
        <ClaudePanel />
        {rows.length === 0 ? (
          <PageState kind="empty" onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : (
          <>
            <Totals rows={rows} />
            <AgentTable rows={rows} now={now} flash={flash} />
          </>
        )}
        <div className="sr-only" aria-live="polite" data-testid="ag-live">
          {announce}
        </div>
      </div>
    </Tooltip.Provider>
  )
}

function AgentTable({ rows, now, flash }: { rows: AgentRow[]; now: number; flash: { slugs: Set<string>; n: number } }) {
  const { t } = useTranslation(NS)
  return (
    <section className="ag-group ag-team" role="table" aria-label={t('title')} id="ag-team" data-testid="ag-team">
      <TeamHead />
      {rows.map((d) => (
        <AgentRowView key={flash.slugs.has(d.slug) ? `${d.slug}-${flash.n}` : d.slug} d={d} now={now} flash={flash.slugs.has(d.slug)} />
      ))}
    </section>
  )
}
