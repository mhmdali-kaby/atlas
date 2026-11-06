import { CircleAlert, Route } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { Button, Skeleton } from '../../ui'
import { MoneyFig } from '../onboarding'
import { useLang } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import { fill } from '../settings-money/parts'
import { EnvScopeControl, type EnvScope } from '../settings-risk/EnvScope'
import type { HistoryEntry } from '../settings-risk/model'
import { ASK_FIELD } from './model'

/* The pieces of Settings → Autonomy (ATL-216) around the page layout. */

export function Section({ id, title, note, children }: { id: string; title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="grid" aria-labelledby={id}>
      <h2 id={id} className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
        {title}
      </h2>
      {children}
      {note && <p className="px-4 pt-2 text-footnote text-label-secondary">{note}</p>}
    </section>
  )
}

export function PaneHeader({ version, scope }: { version: number | null; scope: EnvScope }) {
  const { t } = useTranslation('settings-autonomy')
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Route strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
      <div className="lv-aside">
        {version !== null && (
          <span className="st-version" data-testid="settings-version">
            {t('version', { v: formatFixed(version, 0, lang) })}
          </span>
        )}
        <EnvScopeControl scope={scope} />
      </div>
    </header>
  )
}

export function LevelName({ level }: { level: number }) {
  const { t } = useTranslation('onboarding')
  return <span className="au-now-name">{t(`au.l${level}`)}</span>
}

function ChangeLine({ entry }: { entry: HistoryEntry }) {
  const { t } = useTranslation('settings-autonomy')
  const { t: tOb } = useTranslation('onboarding')
  const level = entry.diff.autonomy_level
  const ask = entry.diff[ASK_FIELD]
  if (level && level.to !== null) {
    const name = tOb(`au.l${level.to}`)
    if (Number(level.to) === 3 && ask && ask.to !== null) {
      return <span>{fill(t('h.levelAsk', { name }), { amount: <MoneyFig value={ask.to} /> })}</span>
    }
    return <span>{t('h.level', { name })}</span>
  }
  if (ask) return <span>{fill(t('h.ask'), { from: <MoneyFig value={ask.from} />, to: <MoneyFig value={ask.to} /> })}</span>
  return null
}

/** The autonomy rows of the settings history, newest first; the seeded version 1 is left out. */
export function Changes({ entries, loading }: { entries: HistoryEntry[] | undefined; loading: boolean }) {
  const { t } = useTranslation('settings-autonomy')
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
  const shown = (entries ?? []).filter((e) => e.version > 1 && ('autonomy_level' in e.diff || ASK_FIELD in e.diff))
  if (shown.length === 0) {
    return (
      <div className="st-group">
        <p className="px-4 py-3 text-subheadline text-label-secondary">{t('historyEmpty')}</p>
      </div>
    )
  }
  return (
    <ol className="st-group" data-testid="autonomy-changes">
      {shown.map((entry, i) => (
        <li key={entry.id} className="st-activity-row" data-version={entry.version}>
          <span className="st-activity-head">
            <span>{t('version', { v: formatFixed(entry.version, 0, lang) })}</span>
            {i === 0 && <span className="st-badge-accent">{t('current')}</span>}
          </span>
          <time dateTime={entry.created_at} className="st-activity-time">
            {formatDayTime(entry.created_at, lang, true)}
          </time>
          <span className="st-activity-lines">
            <ChangeLine entry={entry} />
          </span>
        </li>
      ))}
    </ol>
  )
}

export function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings-autonomy')
  return (
    <section role="alert" className="st-group st-empty" data-testid="autonomy-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('err.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('err.body')}</p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('err.retry')}
      </Button>
    </section>
  )
}

export function LoadingBody() {
  return (
    <div className="grid gap-7" aria-busy="true" data-testid="autonomy-loading">
      <Skeleton className="h-[188px] rounded-card" />
      <div className="grid gap-2">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[72px] rounded-card" />
        ))}
      </div>
    </div>
  )
}
