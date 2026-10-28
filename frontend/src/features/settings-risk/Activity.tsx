import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { Skeleton } from '../../ui'
import { useLang } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import { LimitValue, LIMIT_DEFS } from '../onboarding'
import type { HistoryEntry, LimitField } from './model'

type Change = HistoryEntry['diff'][string]

/** Splits "{{field}} {{from}} → {{to}}" on its placeholders and drops nodes into them. */
function fill(template: string, parts: Record<string, ReactNode>): ReactNode {
  return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
    const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1]
    return <Fragment key={i}>{name ? parts[name] : piece}</Fragment>
  })
}

function ChangeLine({ field, change }: { field: string; change: Change }) {
  const { t } = useTranslation('settings')
  const { t: tOb } = useTranslation('onboarding')
  let label: string
  let render: (v: string | number) => ReactNode
  if (field === 'risk_profile') {
    label = tOb('rp.label')
    render = (v) => tOb(`rp.${v}`)
  } else if (field === 'autonomy_level') {
    label = tOb('au.label')
    render = (v) => tOb(`au.l${v}`)
  } else {
    const def = LIMIT_DEFS.find((d) => d.field === field)
    label = def ? tOb(`lim.${def.key}`) : field
    render = (v) => <LimitValue field={field as LimitField} value={v} />
  }
  const to = change.to === null ? '—' : render(change.to)
  if (change.from === null) return <span>{fill(t('risk.h.set'), { field: label, to })}</span>
  return <span>{fill(t('risk.h.change'), { field: label, from: render(change.from), to })}</span>
}

/** Lines for one version: the fields this page edits, in display order; the rest summarised. */
function changeLines(entry: HistoryEntry) {
  const keys = Object.keys(entry.diff)
  if (entry.version === 1 || keys.every((k) => entry.diff[k]?.from === null)) return { created: true, shown: [], other: false, onboarded: false }
  const order = ['risk_profile', ...LIMIT_DEFS.map((d) => d.field), 'autonomy_level']
  const shown = order.filter((k) => k in entry.diff)
  const onboarded = 'onboarding_completed' in entry.diff && entry.diff.onboarding_completed?.to === 1
  const other = keys.some((k) => !order.includes(k) && k !== 'onboarding_completed')
  return { created: false, shown, other, onboarded }
}

export function ActivityList({ entries, loading, elsewhere }: { entries: HistoryEntry[] | undefined; loading: boolean; elsewhere: ReadonlySet<number> }) {
  const { t } = useTranslation('settings')
  const lang = useLang()

  if (loading && !entries) {
    return (
      <div className="st-group" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="st-activity-row">
            <Skeleton className="h-3.5 w-[30%]" />
            <Skeleton className="col-span-full h-3.5 w-[55%]" />
          </div>
        ))}
      </div>
    )
  }
  if (!entries || entries.length === 0) {
    return (
      <div className="st-group">
        <p className="px-4 py-3 text-subheadline text-label-secondary">{t('risk.activityEmpty')}</p>
      </div>
    )
  }
  return (
    <ol className="st-group" data-testid="activity">
      {entries.map((entry, i) => {
        const lines = changeLines(entry)
        return (
          <li key={entry.id} className="st-activity-row" data-version={entry.version}>
            <span className="st-activity-head">
              <span>{t('risk.version', { v: formatFixed(entry.version, 0, lang) })}</span>
              {i === 0 && <span className="st-badge-accent">{t('risk.current')}</span>}
            </span>
            <time dateTime={entry.created_at} className="st-activity-time">
              {formatDayTime(entry.created_at, lang, true)}
            </time>
            <span className="st-activity-lines">
              {lines.created && <span>{t('risk.h.created')}</span>}
              {lines.shown.map((field) => {
                const change = entry.diff[field]
                return change ? <ChangeLine key={field} field={field} change={change} /> : null
              })}
              {lines.onboarded && <span>{t('risk.h.onboarded')}</span>}
              {lines.other && <span>{t('risk.h.other')}</span>}
              {elsewhere.has(entry.version) && <span className="text-footnote">{t('risk.elsewhere')}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
