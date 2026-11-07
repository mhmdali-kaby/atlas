import { FileDiff, Pin } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Button, Skeleton } from '../../ui'
import { usage, usePromptVersions } from './prompts'
import type { AgentSlug } from './model'
import './prompts.css'

/**
 * The fifth row of each agent's group in Settings → Agents (design v22 §7): which prompt version
 * the agent is told, and History… to the full-width prompt page.
 */
export function PromptRow({ slug }: { slug: AgentSlug }) {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const name = tw(`agents.${slug}.name`)
  const list = usePromptVersions(slug)

  let sub: React.ReactNode = null
  if (list.isPending) sub = <Skeleton className="h-[11px] w-[46%]" />
  else if (list.data) {
    const u = usage(list.data)
    if (list.data.data.length === 0) sub = t('pr.rowEmpty')
    else if (u.pinned !== null)
      sub = (
        <span className="pmt-row-pin">
          <Pin aria-hidden="true" strokeWidth={2} />
          <span>{u.file !== null ? t('pr.rowPinned', { v: u.pinned, n: u.file }) : t('pr.rowPinnedOnly', { v: u.pinned })}</span>
        </span>
      )
    else sub = u.file !== null ? t('pr.rowFile', { n: u.file }) : t('pr.rowNoFile', { name })
  }

  return (
    <div className="sa-row pmt-row" data-testid={`prompt-row-${slug}`}>
      <span className="sa-row-main">
        <span className="sa-row-label">{t('pr.row')}</span>
        {sub !== null && <span className="sa-row-sub">{sub}</span>}
      </span>
      <Button asChild variant="secondary" size="sm">
        <Link to={`/settings/agents/prompts?agent=${slug}`} aria-label={t('pr.rowBtnOf', { name })}>
          <FileDiff aria-hidden="true" strokeWidth={1.75} />
          <span>{t('pr.rowBtn')}</span>
        </Link>
      </Button>
    </div>
  )
}
