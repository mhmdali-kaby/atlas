import { useTranslation } from 'react-i18next'
import type { SidebarSectionProps } from '../../app/feature'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatMoney } from '../../lib/format'
import { Skeleton } from '../../ui'
import { useLang } from '../portfolio/hooks'
import { agentBySlug } from '../warroom/agents'
import { Medallion } from '../warroom/Medallion'
import { useAgentName } from './AgentRow'
import { useAgents } from './api'
import { medalStatus, shownState, sumNotional } from './model'
import { NS, Tr } from './parts'
import '../warroom/warroom.css'
import './agents.css'

/*
 * The Agents area's sidebar (design v9 agents.js sidebar): the team with each agent's state in
 * words and its task; a row jumps to that agent's row on the page. Shares the page's query.
 */

function jumpTo(slug: string) {
  const row = document.getElementById(`ag-row-${slug}`)
  if (!row) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  row.scrollIntoView?.({ block: 'center', behavior: reduce ? 'auto' : 'smooth' })
  row.classList.remove('is-flash')
  void row.offsetWidth
  row.classList.add('is-flash')
  row.focus({ preventScroll: true })
}

export function TeamSidebar({ onNavigate }: SidebarSectionProps) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const names = useAgentName()
  const env = useTradingEnv()
  const q = useAgents(env)
  const rows = q.data?.data

  if (q.isPending) {
    return (
      <div className="side-section" aria-busy="true" aria-label={t('loading')}>
        <h2 className="side-head">
          <span>{t('team')}</span>
        </h2>
        {[96, 120, 88, 104, 132, 92, 110, 124, 100].map((w, i) => (
          <div className="ag-side-skel" key={i}>
            <Skeleton style={{ width: 28, height: 28, borderRadius: '50%' }} />
            <span className="grid gap-1">
              <Skeleton style={{ width: w, height: 12 }} />
              <Skeleton style={{ width: w - 30, height: 10 }} />
            </span>
          </div>
        ))}
      </div>
    )
  }
  if (!rows || rows.length === 0) {
    return (
      <div className="side-section">
        <h2 className="side-head">
          <span>{t('team')}</span>
        </h2>
        <p className="ag-side-none">{rows ? t('sideNone') : t('err.title')}</p>
      </div>
    )
  }

  const cost = formatMoney(sumNotional(rows.map((d) => d.today.cost_usd_notional)), 'USD', lang)
  return (
    <div className="side-section" role="group" aria-labelledby="ag-sh-team" data-testid="ag-sidebar">
      <h2 className="side-head" id="ag-sh-team">
        <span>{t('team')}</span>
        <span className="side-note">
          <Tr k="sideCost" values={{ cost }} />
        </span>
      </h2>
      {rows.map((d) => {
        const st = shownState(d)
        const status = medalStatus(st)
        const name = names.name(d)
        const hasShort = agentBySlug(d.slug)?.hasShort ?? false
        const task = st === 'disabled' ? null : d.current_task
        return (
          <button
            key={d.slug}
            type="button"
            className="agent-row ag-side-row"
            data-nav-item=""
            data-slug={d.slug}
            data-status={status}
            aria-controls={`ag-row-${d.slug}`}
            aria-label={t('rowSr', { name, state: t(`state.${st}`) })}
            onClick={() => {
              jumpTo(d.slug)
              onNavigate()
            }}
          >
            <Medallion slug={d.slug} size={28} status={status} dot />
            <span className="agent-text">
              <span className={hasShort ? 'agent-name has-short' : 'agent-name'}>
                <span className="name-full">{name}</span>
                {hasShort && <span className="name-short">{t(`warroom:agents.${d.slug}.short`)}</span>}
              </span>
              <span className="agent-sub">
                <span className="agent-task">
                  <b>{t(`state.${st}`)}</b>
                  {task && (
                    <>
                      <span className="ag-side-sep">{lang === 'ar' ? '، ' : ', '}</span>
                      <bdi>{task}</bdi>
                    </>
                  )}
                </span>
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
