import { useQueryClient } from '@tanstack/react-query'
import { CloudOff, RefreshCw, Rss } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useCallback, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Skeleton, toast } from '../../ui'
import { useNow } from '../portfolio/hooks'
import { SOURCES_KEY, useSources, useToggleSource, type ToggleVars, type ToggleFailure } from './api'
import type { KeyFailure } from './api'
import { KeySheet, KeysGroup, RemoveKeySheet, type KeyTarget } from './KeySheets'
import {
  FEED_CATEGORIES,
  groups,
  keyProviders,
  keyStartedBy,
  providerName,
  startedBy,
  summary,
  withKey,
  type ApiSource,
  type NoticeCategory,
  type SourcesReport,
  type StartedAt,
} from './model'
import { NS, Tr, useSourceName, useTimes } from './parts'
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
  const time = useTimes(now)

  /* ---- keys (ATL-69) ---- */
  // A fresh `n` per opening remounts the sheet clean; `open: false` keeps it while it animates out.
  type SheetState = { target: KeyTarget; open: boolean; n: number } | null
  const [keySheet, setKeySheet] = useState<SheetState>(null)
  const [removeSheet, setRemoveSheet] = useState<SheetState>(null)
  const show = (target: KeyTarget | null) => (prev: SheetState): SheetState =>
    target ? { target, open: true, n: (prev?.n ?? 0) + 1 } : prev
  const hide = (prev: SheetState): SheetState => (prev ? { ...prev, open: false } : prev)
  // Where focus goes when a key sheet closes after a change (design §5.6); null: back to the trigger.
  const focusNext = useRef<string | null>(null)
  // The control that opened the sheet: Radix only returns focus to its own Trigger, and ours has none.
  const opener = useRef<HTMLElement | null>(null)
  const remember = () => {
    focusNext.current = null
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
  }

  const targetFor = (provider: string, replace: boolean): KeyTarget | null => {
    const s = qc.getQueryData<SourcesReport>(SOURCES_KEY)?.data.find((x) => x.requires_key && x.provider === provider)
    return s ? { provider, sourceKey: s.key, replace: replace && s.has_key } : null
  }
  const openKey = (provider: string, replace: boolean) => {
    remember()
    setKeySheet(show(targetFor(provider, replace)))
  }
  const openRemove = (provider: string) => {
    remember()
    setRemoveSheet(show(targetFor(provider, false)))
  }
  const onKeys = (providers: string[]) => {
    if (providers.length === 1) {
      openKey(providers[0]!, false)
      return
    }
    const sec = document.getElementById('src-keys')
    if (!sec) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    sec.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
    sec.querySelector<HTMLElement>('#src-keys-title')?.focus({ preventScroll: true })
  }
  const onCloseAutoFocus = (e: Event) => {
    const sel = focusNext.current
    focusNext.current = null
    const el = (sel ? document.querySelector<HTMLElement>(sel) : null) ?? (opener.current?.isConnected ? opener.current : null)
    opener.current = null
    if (!el) return
    e.preventDefault()
    el.focus()
  }

  /** After a key change: the cache takes the provider-wide edit, the affected rows show Starting, then a refetch. */
  const applyKey = (provider: string, last4: string | null) => {
    const before = qc.getQueryData<SourcesReport>(SOURCES_KEY)
    if (!before) return
    const owned = before.data.filter((s) => s.requires_key && s.provider === provider).map((s) => s.key)
    const started = keyStartedBy(before, provider, last4 !== null)
    const at = Date.now()
    setStartedAt((m) => {
      const next: Record<string, number> = { ...m }
      if (last4 === null) for (const k of owned) delete next[k]
      for (const k of started) next[k] = at
      return next
    })
    qc.setQueryData<SourcesReport>(SOURCES_KEY, withKey(before, provider, last4, at))
    void qc.invalidateQueries({ queryKey: SOURCES_KEY })
  }

  const onKeySaved = (provider: string, saved: ApiSource) => {
    const name = providerName(provider)
    const polls = qc
      .getQueryData<SourcesReport>(SOURCES_KEY)
      ?.data.filter((s) => s.requires_key && s.provider === provider)
      .map((s) => s.poll_seconds) ?? [saved.poll_seconds]
    applyKey(provider, saved.key_last4 ?? '')
    focusNext.current = `#src-key-${provider} [data-key-action="replace"]`
    setKeySheet(hide)
    toast({ tone: 'success', title: t('ks.saved', { provider: name }), body: t('ks.savedBody', { provider: name, within: time.within(Math.min(...polls)) }) })
  }

  const onKeyRemoved = (provider: string, failure: KeyFailure | null) => {
    const name = providerName(provider)
    if (failure === null) {
      applyKey(provider, null)
      focusNext.current = `#src-key-${provider} [data-key-action="add"]`
    }
    setRemoveSheet(hide)
    if (failure === null) toast({ tone: 'success', title: t('rm.toast', { provider: name }) })
    else if (failure === 'limited') toast({ tone: 'warning', title: t('limitedToast') })
    else toast({ tone: 'error', title: t('rm.failed'), body: t('ks.down.body') })
  }

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
        <Summary sm={sm!} rows={report.data} now={now} updatedAt={sources.dataUpdatedAt} onShow={showGroup} onKeys={onKeys} />
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
                return <SourceRow key={s.key} s={s} st={st} now={now} stale={stale} onToggle={onToggle} onKey={openKey} />
              })}
            </div>
            <p className="src-group-note">{t(`note.${g.cat}`)}</p>
          </section>
        ))}
        <KeysGroup providers={keyProviders(report.data)} onAdd={openKey} onRemove={openRemove} />
      </>
    )

  return (
    <Tooltip.Provider delayDuration={450}>
      <div className="st-page src-page" data-testid="sources-page">
        <Pane />
        {body}
      </div>
      {keySheet && (
        <KeySheet
          key={keySheet.n}
          target={keySheet.target}
          open={keySheet.open}
          onOpenChange={(o) => !o && setKeySheet(hide)}
          onSaved={onKeySaved}
          onCloseAutoFocus={onCloseAutoFocus}
        />
      )}
      {removeSheet && (
        <RemoveKeySheet
          key={removeSheet.n}
          target={removeSheet.target}
          open={removeSheet.open}
          onOpenChange={(o) => !o && setRemoveSheet(hide)}
          onDone={onKeyRemoved}
          onCloseAutoFocus={onCloseAutoFocus}
        />
      )}
    </Tooltip.Provider>
  )
}
