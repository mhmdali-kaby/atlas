import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { Skeleton } from '../../ui'
import type { HistoryEntry } from '../settings-risk/model'
import { formatDayTime } from '../portfolio/time'
import { groupText, MONEY_FIELDS, type MoneyField } from './model'
import { Fig, fill } from './parts'

/* The last versions that touched money limits (design §5.5: "Bot capital cap: 100,000 USDT → no cap"). */

function Value({ field, value, lang }: { field: MoneyField; value: string | number | null; lang: Lang }) {
  const { t } = useTranslation('settings-money')
  if (value === null || value === '') return <>{t('h.noCap')}</>
  if (field === 'max_trades_per_day') return <Fig>{formatFixed(value, 0, lang)}</Fig>
  return <Fig>{`${groupText(field, String(value))} USDT`}</Fig>
}

export function MoneyActivity({ entries, loading, lang }: { entries: HistoryEntry[] | undefined; loading: boolean; lang: Lang }) {
  const { t } = useTranslation('settings-money')
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
        <p className="px-4 py-3 text-subheadline text-label-secondary">{t('activityEmpty')}</p>
      </div>
    )
  }
  return (
    <ol className="st-group" data-testid="ml-activity">
      {entries.slice(0, 5).map((entry, i) => {
        const keys = Object.keys(entry.diff)
        const created = entry.version === 1 || keys.every((k) => entry.diff[k]?.from === null)
        const shown = MONEY_FIELDS.filter((f) => f in entry.diff)
        const other = keys.some((k) => !(MONEY_FIELDS as readonly string[]).includes(k))
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
              {!created &&
                shown.map((f) => {
                  const c = entry.diff[f]
                  if (!c) return null
                  return (
                    <span key={f}>
                      {fill(t('h.change'), {
                        field: t(`f.${f}`),
                        from: <Value field={f} value={c.from} lang={lang} />,
                        to: <Value field={f} value={c.to} lang={lang} />,
                      })}
                    </span>
                  )
                })}
              {!created && other && <span>{t('h.other')}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
