import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, formatList, type Lang } from '../../lib/format'
import { Skeleton } from '../../ui'
import { formatDayTime } from '../portfolio/time'
import { Fig, fill } from '../settings-money/parts'
import type { HistoryEntry } from '../settings-risk/model'
import { FIELD_KEYS, grouped, KINDS, VOLUME_MARKETS, volKey, type FieldKey } from './model'

/* The last versions that touched Markets settings, in words (design v14 settings-markets.js activity). */

const asList = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : [])

function Value({ field, value, lang }: { field: FieldKey; value: unknown; lang: Lang }) {
  const { t } = useTranslation('settings-markets')
  if (value === null || value === undefined || value === '') return <>—</>
  if (field === 'avoid_stock_perps_outside_us_hours') return <>{t(value ? 'h.yes' : 'h.no')}</>
  if (field === 'max_instruments_per_day') return <Fig>{formatFixed(String(value), 0, lang)}</Fig>
  if (field === 'max_spread_bps') return <Fig>{`${String(value)} bps`}</Fig>
  return <Fig>{`$${grouped(String(value))}`}</Fig>
}

function lines(field: FieldKey, from: unknown, to: unknown, lang: Lang, t: (k: string, o?: Record<string, unknown>) => string): ReactNode[] {
  if (field === 'whitelist' || field === 'blocklist') {
    const a = asList(from)
    const b = asList(to)
    const added = b.filter((x) => !a.includes(x))
    const removed = a.filter((x) => !b.includes(x))
    const bits: string[] = []
    if (added.length) bits.push(t('h.added', { list: formatList(added, lang) }))
    if (removed.length) bits.push(t('h.removed', { list: formatList(removed, lang) }))
    if (!bits.length) return []
    const what = bits.length === 2 ? t('h.join', { a: bits[0], b: bits[1] }) : bits[0]
    return [t('h.listLine', { field: t(field === 'whitelist' ? 'allow' : 'deny'), what })]
  }
  if (field === 'enabled_markets') {
    const a = asList(from)
    const b = asList(to)
    return [
      ...KINDS.filter((k) => a.includes(k) && !b.includes(k)).map((k) => t('h.off', { market: t(`m.${k}`) })),
      ...KINDS.filter((k) => !a.includes(k) && b.includes(k)).map((k) => t('h.on', { market: t(`m.${k}`) })),
    ]
  }
  const market = VOLUME_MARKETS.find((m) => volKey(m) === field)
  const name = market ? t('h.vol', { market: t(`m.${market}`) }) : field === 'max_spread_bps' ? t('maxSpread') : field === 'max_instruments_per_day' ? t('wlMax') : t('avoid')
  return [
    fill(t('h.change'), {
      field: name,
      from: <Value field={field} value={from} lang={lang} />,
      to: <Value field={field} value={to} lang={lang} />,
    }),
  ]
}

export function MarketsActivity({ entries, loading, lang }: { entries: HistoryEntry[] | undefined; loading: boolean; lang: Lang }) {
  const { t } = useTranslation('settings-markets')
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
  const tt = t as unknown as (k: string, o?: Record<string, unknown>) => string
  const shown = (entries ?? []).slice(0, 5)
  if (shown.length === 0) {
    return (
      <div className="st-group">
        <p className="px-4 py-3 text-subheadline text-label-secondary">{t('activityEmpty')}</p>
      </div>
    )
  }
  return (
    <ol className="st-group" data-testid="mk-activity">
      {shown.map((entry, i) => {
        const diff = entry.diff as Record<string, { from: unknown; to: unknown }>
        const keys = Object.keys(diff)
        const created = entry.version === 1 || keys.every((k) => diff[k]?.from === null)
        const mine = FIELD_KEYS.filter((f) => f in diff)
        const other = keys.some((k) => !(FIELD_KEYS as readonly string[]).includes(k))
        const said = created ? [] : mine.flatMap((f) => lines(f, diff[f]?.from, diff[f]?.to, lang, tt))
        return (
          <li key={entry.id} className="st-activity-row" data-version={entry.version}>
            <span className="st-activity-head">
              <span>{t('version', { v: formatFixed(entry.version, 0, lang) })}</span>
              {i === 0 && <span className="st-badge-accent">{t('current')}</span>}
            </span>
            <time dateTime={entry.created_at} className="st-activity-time">
              {formatDayTime(entry.created_at, lang, true)}
            </time>
            <span className="st-activity-lines">
              {created && <span>{t('h.created')}</span>}
              {said.map((l, j) => (
                <span key={j}>{l}</span>
              ))}
              {!created && other && <span>{t('h.other')}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
