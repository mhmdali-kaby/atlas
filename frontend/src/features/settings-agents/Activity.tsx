import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { Skeleton } from '../../ui'
import { useLang } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import type { HistoryEntry } from '../settings-risk/model'
import { displayHost, LISTS } from './model'

/*
 * Activity (design v9 settings-agents.js activity()): the settings versions that changed an
 * agents.* field, newest first, in words. Diff values can be booleans or arrays (ATL-113).
 */

type Diff = Record<string, { from: unknown; to: unknown }>

const isAgentKey = (k: string) => k.startsWith('agents.')
const asList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

function Line({ field, change }: { field: string; change: { from: unknown; to: unknown } }) {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const lang = useLang()
  const parts = field.split('.')
  if (field === LISTS.allow || field === LISTS.deny) {
    const from = asList(change.from)
    const to = asList(change.to)
    const fmt = new Intl.ListFormat(lang === 'ar' ? 'ar' : 'en', { style: 'long', type: 'conjunction' })
    const hosts = (xs: string[]) => fmt.format(xs.map((h) => `⁦${displayHost(h)}⁩`))
    const added = to.filter((h) => !from.includes(h))
    const removed = from.filter((h) => !to.includes(h))
    const bits = [
      ...(added.length ? [t('h.added', { list: hosts(added) })] : []),
      ...(removed.length ? [t('h.removed', { list: hosts(removed) })] : []),
    ]
    const what = bits.length === 2 ? t('h.join', { a: bits[0], b: bits[1] }) : (bits[0] ?? '')
    return <span>{t('h.listLine', { field: t(field === LISTS.allow ? 'allow' : 'deny'), what })}</span>
  }
  const slug = parts[1] ?? ''
  const f = parts[2] ?? ''
  const name = tw(`agents.${slug}.name`, { defaultValue: slug })
  if (f === 'enabled') return <span>{t(change.to === true ? 'h.on' : 'h.off', { name })}</span>
  const what = t(f === 'model' ? 'h.model' : f === 'daily_tokens' ? 'h.tokens' : 'h.turns')
  const val = (v: unknown) =>
    f === 'model' && typeof v === 'string' ? t(`m.${v}`, { defaultValue: v }) : typeof v === 'number' ? `⁦${formatFixed(v, 0, lang)}⁩` : String(v ?? '—')
  return <span>{t('h.change', { field: t('h.field', { name, what }), from: val(change.from), to: val(change.to) })}</span>
}

export function AgentActivity({ entries, loading, elsewhere }: { entries: HistoryEntry[] | undefined; loading: boolean; elsewhere: ReadonlySet<number> }) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()

  if (loading && !entries) {
    return (
      <div className="st-group" aria-busy="true">
        {[0, 1].map((i) => (
          <div key={i} className="st-activity-row">
            <Skeleton className="h-3.5 w-[30%]" />
            <Skeleton className="col-span-full h-3.5 w-[55%]" />
          </div>
        ))}
      </div>
    )
  }
  const mine = (entries ?? []).filter((e) => Object.keys(e.diff).some(isAgentKey)).slice(0, 5)
  if (mine.length === 0) {
    return (
      <div className="st-group">
        <p className="px-4 py-3 text-subheadline text-label-secondary">{t('activityEmpty')}</p>
      </div>
    )
  }
  const newest = entries?.[0]?.version
  return (
    <ol className="st-group" data-testid="agents-activity">
      {mine.map((entry) => {
        const diff = entry.diff as unknown as Diff
        const keys = Object.keys(diff)
        const shown = keys.filter(isAgentKey)
        return (
          <li key={entry.id} className="st-activity-row" data-version={entry.version}>
            <span className="st-activity-head">
              <span>{t('version', { v: formatFixed(entry.version, 0, lang) })}</span>
              {entry.version === newest && <span className="st-badge-accent">{t('current')}</span>}
            </span>
            <time dateTime={entry.created_at} className="st-activity-time">
              {formatDayTime(entry.created_at, lang, true)}
            </time>
            <span className="st-activity-lines">
              {shown.map((k) => {
                const change = diff[k]
                return change ? <Line key={k} field={k} change={change} /> : null
              })}
              {shown.length < keys.length && <span>{t('h.other')}</span>}
              {elsewhere.has(entry.version) && <span className="text-footnote">{t('merged.title', { v: formatFixed(entry.version, 0, lang) })}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
