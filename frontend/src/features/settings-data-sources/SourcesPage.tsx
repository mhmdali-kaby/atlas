import { useQueryClient } from '@tanstack/react-query'
import { CloudOff, RefreshCw, Rss } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Skeleton, toast } from '../../ui'
import { useNow } from '../portfolio/hooks'
import { SOURCES_KEY, useSources, useToggleSource, type ToggleVars, type ToggleFailure } from './api'
import { FEED_CATEGORIES, groups, startedBy, summary, type ApiSource, type NoticeCategory, type SourcesReport, type StartedAt } from './model'
import { NS, Tr, useSourceName } from './parts'
import { SourceRow } from './SourceRow'
import { Summary } from './Summary'
import '../settings-connections/settings.css'
import './sources.css'

/*
 * Settings → Data sources (ATL-68; design "Atlas design system: Data sources and news alerts").
 * Every news, calendar and sentiment source Atlas reads, grouped by what it gives Atlas: its health
 * in words, a 24-hour headline strip for the feeds, and an on/off switch. "The wire" leads the page.
 * Polls every 30 s and updates in place. The Keys group and key sheets are ATL-69's.
 */

function Pane() {
  const { t } = useTranslation(NS)
  return (
    <header className="st-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Rss strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
    </header>
  )
}

function Loading() {
  const rows = (n: number) => (
    <div className="src-group src-sk-rows">
      {Array.from({ length: n }, (_, k) => (
        <span className="src-sk-row" key={k}>
          <Skeleton className="src-sk-mono" />
          <span className="src-sk-lines">
            <Skeleton style={{ width: '38%' }} />
            <Skeleton style={{ width: '62%', height: 10 }} />
          </span>
          <Skeleton className="src-sk-strip" />
          <Skeleton className="src-sk-switch" />
        </span>
      ))}
    </div>
  )
  return (
    <div className="src-loading" aria-busy="true" data-testid="src-loading">
      <section className="src-group src-summary">
        <div className="src-sum-head">
          <Skeleton style={{ width: 40, height: 40, borderRadius: 11 }} />
          <div className="grid gap-2">
            <Skeleton style={{ width: 240, height: 16, maxWidth: '100%' }} />
            <Skeleton style={{ width: 320, height: 12, maxWidth: '100%' }} />
          </div>
        </div>
        <Skeleton className="src-sk-chart" />
      </section>
      {[1, 4, 3].map((n, i) => (
        <section className="src-section" key={i}>
          <Skeleton className="src-sk-title" />
          {rows(n)}
        </section>
      ))}
    </div>
  )
}

function StateCard({ kind, onRetry }: { kind: 'error' | 'empty'; onRetry: () => void }) {
  const { t } = useTranslation(NS)
  const key = kind === 'error' ? 'loadError' : 'empty'
  return (
    <section className="src-group st-empty" role={kind === 'error' ? 'alert' : undefined} data-testid={`src-${kind}`}>
      <span className={kind === 'error' ? 'st-empty-icon is-error' : 'st-empty-icon'} aria-hidden="true">
        {kind === 'error' ? <CloudOff strokeWidth={1.5} /> : <Rss strokeWidth={1.5} />}
      </span>
      <h2 className="text-title-3">{t(`${key}.title`)}</h2>
      <p className="max-w-[52ch] text-subheadline text-label-secondary">
        <Tr k={`${key}.body`} />
      </p>
      <Button variant="secondary" icon={<RefreshCw />} className="mt-2" onClick={onRetry}>
        {t('retry')}
      </Button>
    </section>
  )
}

export function SourcesPage() {
  const { t } = useTranslation(NS)
  const nameOf = useSourceName()
  const qc = useQueryClient()
  const sources = useSources()
  const now = useNow(15_000)
  const [startedAt, setStartedAt] = useState<StartedAt>({})

  const onFailed = useCallback(
    (vars: ToggleVars, failure: ToggleFailure) => {
      setStartedAt((m) => {
        const next = { ...m }
        delete next[vars.key]
        return next
      })
      if (failure === 'limited') {
        toast({ tone: 'warning', title: t('limitedToast') })
        return
      }
      const s = sources.data?.data.find((x) => x.key === vars.key)
      const name = s ? nameOf(s) : vars.key
      toast({ tone: 'error', title: t(vars.enabled ? 'sw.failOn' : 'sw.failOff', { name }), body: t('sw.failBody') })
    },
    [sources.data, nameOf, t],
  )
  const toggle = useToggleSource(onFailed)

  const onToggle = (s: ApiSource, enabled: boolean) => {
    const before = qc.getQueryData<SourcesReport>(SOURCES_KEY)
    const started = before ? startedBy(before, s.key, enabled) : enabled ? [s.key] : []
    const at = Date.now()
    setStartedAt((m) => {
      const next: Record<string, number> = { ...m }
      if (!enabled) delete next[s.key]
      for (const k of started) next[k] = at
      return next
    })
    toggle.mutate({ key: s.key, enabled })
  }

  const report = sources.data
  // Rows clear their Starting once the collector's first check is newer than the moment they were turned on.
  const sm = useMemo(() => (report ? summary(report, now, startedAt) : null), [report, now, startedAt])
  const stale = sm?.state === 'stalled'

  const showGroup = (cat: NoticeCategory) => {
    const sec = document.getElementById(`src-g-${cat}`)
    if (!sec) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    sec.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
    sec.querySelector<HTMLElement>('.src-group-title')?.focus({ preventScroll: true })
  }

  let body
  if (sources.isPending) body = <Loading />
  else if (!report) body = <StateCard kind="error" onRetry={() => void sources.refetch()} />
  else if (report.data.length === 0) body = <StateCard kind="empty" onRetry={() => void sources.refetch()} />
  else
    body = (
      <>
        <Summary sm={sm!} rows={report.data} now={now} updatedAt={sources.dataUpdatedAt} onShow={showGroup} />
        {groups(report.data).map((g) => (
          <section className="src-section" id={`src-g-${g.cat}`} key={g.cat} aria-labelledby={`src-gt-${g.cat}`} data-testid="src-group" data-cat={g.cat}>
            <div className="src-group-head">
              <h2 className="src-group-title" id={`src-gt-${g.cat}`} tabIndex={-1}>
                {t(`cat.${g.cat}`)}
              </h2>
              {FEED_CATEGORIES.has(g.cat) && (
                <span className="src-col-cap" aria-hidden="true">
                  {t('last24')}
                </span>
              )}
            </div>
            <div className="src-group">
              {g.rows.map((s) => {
                const st = sm!.rows.find((x) => x.s.key === s.key)!.st
                return <SourceRow key={s.key} s={s} st={st} now={now} stale={stale} onToggle={onToggle} />
              })}
            </div>
            <p className="src-group-note">{t(`note.${g.cat}`)}</p>
          </section>
        ))}
      </>
    )

  return (
    <Tooltip.Provider delayDuration={450}>
      <div className="st-page src-page" data-testid="sources-page">
        <Pane />
        {body}
      </div>
    </Tooltip.Provider>
  )
}
