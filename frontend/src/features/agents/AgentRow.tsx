import { CircleCheck, CircleDot, CircleX, Gauge, Hourglass, Minus, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Spinner } from '../../ui'
import { useLang } from '../portfolio/hooks'
import { Medallion } from '../warroom/Medallion'
import type { AgentRow as Agent } from './api'
import { compact, isWarnState, medalStatus, meter, shownState, type ShownState } from './model'
import { Fig, NS, Tip, Tr, useWhen } from './parts'
import { formatFixed, formatMoney } from '../../lib/format'

/*
 * One agent's row (design v9 agents.js rowHtml): who it is, its state in words, today's usage
 * against the daily budget, model and prompt version, and the last run.
 */

export function useAgentName() {
  const { t } = useTranslation('warroom')
  return useMemo(
    () => ({
      name: (d: Pick<Agent, 'slug' | 'name'>) => t(`agents.${d.slug}.name`, { defaultValue: d.name }),
      role: (d: Pick<Agent, 'slug'>) => t(`agents.${d.slug}.role`, { defaultValue: '' }),
    }),
    [t],
  )
}

export function StateWord({ st }: { st: ShownState }) {
  const { t } = useTranslation(NS)
  return <>{t(`state.${st}`)}</>
}

function StatusCell({ d, st, now }: { d: Agent; st: ShownState; now: number }) {
  const { t } = useTranslation(NS)
  const when = useWhen(now)
  if (st === 'disabled') {
    return (
      <>
        <p className="ag-state is-off" data-testid="ag-state">
          <span className="ag-dot" data-status="offline" aria-hidden="true" />
          <b>{t('state.disabled')}</b>
        </p>
        <Link className="ag-link" to="/settings/agents">
          {t('turnOn')}
        </Link>
      </>
    )
  }
  const since = d.state_since ? Date.parse(d.state_since) : null
  return (
    <>
      <p className={isWarnState(st) ? 'ag-state is-warn' : 'ag-state'} data-testid="ag-state">
        <span className="ag-dot" data-status={medalStatus(st)} aria-hidden="true" />
        <b>{t(`state.${st}`)}</b>
        {since !== null && (
          <Tip label={when.full(since)}>
            <span className="ag-since" tabIndex={0}>
              <Tr k="since" values={{ time: when.short(since) }} components={{ tm: <time className="num" dateTime={d.state_since ?? undefined} /> }} />
            </span>
          </Tip>
        )}
      </p>
      {d.current_task && (
        <p className="ag-task" dir="auto" title={d.current_task}>
          {d.current_task}
        </p>
      )}
    </>
  )
}

function MeterLine({ kind, used, budget }: { kind: 'tokens' | 'turns'; used: number; budget: number }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const m = meter(used, budget)
  const fmt = (n: number) => (kind === 'tokens' ? compact(n, lang) : formatFixed(n, 0, lang))
  const sr = t('meterSr', { what: t(kind), used: fmt(used), budget: fmt(budget), pct: `${m.pct}%` })
  const cls = ['ag-meter', m.warn && 'is-warn', m.full && 'is-full'].filter(Boolean).join(' ')
  return (
    <div className={cls} role="img" aria-label={sr} data-testid={`ag-meter-${kind}`} data-pct={m.pct} data-warn={m.warn ? 'true' : 'false'}>
      <p className="ag-meter-t" aria-hidden="true">
        <span className="ag-meter-k">{t(kind)}</span>
        <span className="ag-meter-v">
          <Tr k="of" values={{ used: fmt(used), budget: fmt(budget) }} />
        </span>
        {m.warn && <Fig className="ag-pct">{`${m.pct}%`}</Fig>}
      </p>
      <span className="ag-bar" aria-hidden="true">
        <i style={{ width: `${m.fill}%` }} />
      </span>
    </div>
  )
}

function TodayCell({ d }: { d: Agent }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  if (!d.enabled && !d.today.runs) return <p className="ag-muted">{t('noRuns')}</p>
  return (
    <>
      <div className="ag-meters">
        <MeterLine kind="tokens" used={d.today.tokens} budget={d.budget.daily_tokens} />
        <MeterLine kind="turns" used={d.today.turns} budget={d.budget.daily_turns} />
      </div>
      {d.today.runs > 0 && (
        <p className="ag-cost">
          <Tip label={t('costTip')}>
            <span tabIndex={0} data-tip="" data-testid="ag-cost">
              <Tr k="cost" values={{ cost: formatMoney(d.today.cost_usd_notional, 'USD', lang) }} />
            </span>
          </Tip>
        </p>
      )}
    </>
  )
}

function ModelCell({ d }: { d: Agent }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const name = d.model_name ? d.model_name.charAt(0).toUpperCase() + d.model_name.slice(1) : ''
  return (
    <>
      <p className="ag-model">
        <b>{t(`model.${d.model}`, { defaultValue: d.model })}</b>
        {name && <bdi className="ag-model-name">{name}</bdi>}
      </p>
      {d.prompt ? (
        <p className="ag-prompt" data-testid="ag-prompt">
          <span className="ag-prompt-k">{t('prompt')}</span>
          <Fig>{`v${d.prompt.sequence}`}</Fig>
          <Tip label={t('promptTip', { n: d.prompt.sequence, date: formatDateShort(d.prompt.created_at, lang) })}>
            <code dir="ltr" tabIndex={0}>
              {d.prompt.sha}
            </code>
          </Tip>
        </p>
      ) : (
        <p className="ag-muted">{t('noPrompt')}</p>
      )}
    </>
  )
}

function formatDateShort(iso: string, lang: 'en' | 'ar'): string {
  return new Intl.DateTimeFormat(lang === 'ar' ? 'ar-IQ-u-nu-latn' : 'en-GB', {
    timeZone: 'Asia/Baghdad',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(iso))
}

const RUN_ICON: Record<string, LucideIcon> = {
  ok: CircleCheck,
  error: CircleX,
  degraded: TriangleAlert,
  rate_limited: Hourglass,
  over_budget: Gauge,
}

function RunCell({ d, now }: { d: Agent; now: number }) {
  const { t } = useTranslation(NS)
  const when = useWhen(now)
  const r = d.last_run
  if (!r) {
    return (
      <p className="ag-run is-none">
        <Minus strokeWidth={2} aria-hidden="true" />
        <span>{t('run.none')}</span>
      </p>
    )
  }
  const Icon = RUN_ICON[r.status] ?? CircleDot
  const at = Date.parse(r.ended_at ?? r.started_at)
  return (
    <>
      <p className={`ag-run is-${r.status}`} data-testid="ag-run">
        {r.status === 'running' ? <Spinner /> : <Icon strokeWidth={2} aria-hidden="true" />}
        <b>{t(`run.${r.status}`, { defaultValue: r.status })}</b>
      </p>
      <Tip label={when.full(at)}>
        <p className="ag-run-when" tabIndex={0}>
          <Tr
            k="run.when"
            values={{ time: when.short(at), trigger: t(`trig.${r.trigger}`, { defaultValue: r.trigger }) }}
            components={{ tm: <time className="num" dateTime={new Date(at).toISOString()} /> }}
          />
        </p>
      </Tip>
    </>
  )
}

export function AgentRowView({ d, now, flash }: { d: Agent; now: number; flash: boolean }) {
  const { t } = useTranslation(NS)
  const names = useAgentName()
  const st = shownState(d)
  const name = names.name(d)
  const cls = ['ag-row', st === 'disabled' && 'is-off'].filter(Boolean).join(' ')
  return (
    <div
      className={cls}
      role="row"
      id={`ag-row-${d.slug}`}
      tabIndex={-1}
      aria-label={t('rowSr', { name, state: t(`state.${st}`) })}
      data-testid="ag-row"
      data-slug={d.slug}
      data-state={st}
    >
      <div className="ag-c ag-c-agent" role="cell">
        <Medallion slug={d.slug} size={36} status={medalStatus(st)} />
        <span className="ag-who">
          <b>{name}</b>
          <span>{names.role(d)}</span>
        </span>
      </div>
      <div className={flash ? 'ag-c ag-c-status is-flash' : 'ag-c ag-c-status'} role="cell">
        <StatusCell d={d} st={st} now={now} />
      </div>
      <div className="ag-c ag-c-today" role="cell">
        <TodayCell d={d} />
      </div>
      <div className="ag-c ag-c-model" role="cell">
        <ModelCell d={d} />
      </div>
      <div className="ag-c ag-c-run" role="cell">
        <RunCell d={d} now={now} />
      </div>
    </div>
  )
}
