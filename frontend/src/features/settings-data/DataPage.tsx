import { CloudOff, Database, Play, RefreshCw } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Skeleton, toast } from '../../ui'
import { useNow } from '../portfolio/hooks'
import { answerOf, useBackfill, useDataAction, useStorage, type ActionAnswer, type BackfillReport } from './api'
import { InstrumentSheet } from './InstrumentSheet'
import { Instruments } from './Instruments'
import { Keeps } from './Keeps'
import {
  buildModel,
  bytesText,
  etaSeconds,
  FALLBACK_DEPTH_DAYS,
  summaryState,
  type DatasetKey,
  type InstFilter,
  type Sample,
} from './model'
import { NS, Tr, useDepthText } from './parts'
import { Quality } from './Quality'
import { Notice, Summary, type BusyKind } from './Summary'
import './data.css'

/*
 * Settings → Data (brief §14.10, design "Atlas design system: Settings → Data"): how far the
 * market-history backfill has got, what is wrong with the data, what Atlas keeps, and every
 * instrument's jobs. Progress polls every 5 s while a job runs and updates in place.
 */

function Pane() {
  const { t } = useTranslation(NS)
  return (
    <header className="st-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Database strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav.data')}</h2>
        <p className="max-w-[60ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
    </header>
  )
}

function Loading() {
  return (
    <div className="dt-loading" aria-busy="true" data-testid="dt-loading">
      <section className="dt-group dt-summary">
        <div className="dt-sum-head">
          <Skeleton className="size-10 rounded-[11px]" />
          <div className="grid gap-2">
            <Skeleton className="dt-sk-line w-[260px]" />
            <Skeleton className="dt-sk-line h-3 w-[420px] max-w-full" />
          </div>
          <Skeleton className="h-8 w-24 rounded-control" />
        </div>
        <Skeleton className="dt-sk-map" />
        <Skeleton className="dt-sk-line h-3 w-[320px]" />
      </section>
      {[5, 4].map((n) => (
        <section className="dt-section" key={n}>
          <Skeleton className="dt-sk-line mx-4 mb-2.5 w-32" />
          <div className="dt-group dt-sk-rows">
            {Array.from({ length: n }, (_, k) => (
              <Skeleton key={k} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation(NS)
  return (
    <section role="alert" className="dt-group st-empty" data-testid="dt-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CloudOff strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('error.title')}</h2>
      <p className="max-w-[52ch] text-subheadline text-label-secondary">
        <Tr k="error.body" />
      </p>
      <Button variant="secondary" icon={<RefreshCw />} className="mt-2" onClick={onRetry}>
        {t('retry')}
      </Button>
    </section>
  )
}

function NotStarted({
  busy,
  notice,
  onStart,
}: {
  busy: boolean
  notice: Exclude<ActionAnswer, 'ok'> | null
  onStart: () => void
}) {
  const { t } = useTranslation(NS)
  const depthText = useDepthText()
  const plan: [string, DatasetKey][] = [
    ['1D', 'candles:1D'],
    ['1H', 'candles:1H'],
    ['15m', 'candles:15m'],
    ['1m', 'candles:1m'],
    ['open', 'funding'],
  ]
  return (
    <section className="dt-group dt-empty" aria-labelledby="dt-empty-title" data-testid="dt-not-started">
      <span className="st-empty-icon" aria-hidden="true">
        <Database strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3" id="dt-empty-title">
        {t('empty.title')}
      </h2>
      <p className="max-w-[56ch] text-subheadline text-label-secondary">{t('empty.body')}</p>
      <Button variant="primary" icon={<Play />} loading={busy} data-testid="dt-start" onClick={() => !busy && onStart()}>
        {t(busy ? 'starting' : 'start')}
      </Button>
      {notice && (
        <div className="dt-empty-notice">
          <Notice answer={notice} onRetry={onStart} />
        </div>
      )}
      <div className="dt-plan">
        {plan.map(([k, key]) => (
          <div className="dt-plan-row" key={k}>
            <span>{t(`empty.plan.${k}`)}</span>
            <span>{depthText(key, FALLBACK_DEPTH_DAYS[key])}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

/** The configured depth per dataset, from the first job that reports one (else defaults.yaml's values). */
function depthLookup(report: BackfillReport | undefined) {
  const found = new Map<string, number | null>()
  for (const i of report?.instruments ?? []) for (const j of i.datasets) if (!found.has(j.dataset)) found.set(j.dataset, j.depth_days)
  return (k: DatasetKey) => (found.has(k) ? found.get(k)! : FALLBACK_DEPTH_DAYS[k])
}

/** After an action, poll until the server reflects it, at most this long. */
const AFTER_ACTION_MS = 60_000

export function DataPage() {
  const { t } = useTranslation(NS)
  const [pollUntil, setPollUntil] = useState(0)
  const nowMs = useNow(5_000)
  const backfill = useBackfill(pollUntil)
  const storage = useStorage()
  const action = useDataAction()

  const model = useMemo(() => (backfill.data ? buildModel(backfill.data) : null), [backfill.data])
  // Server time, advanced by the client time since that poll (stalled compares it with jobs' updated_at).
  const serverNow = backfill.data ? Date.parse(backfill.data.as_of) + Math.max(0, nowMs - backfill.dataUpdatedAt) : nowMs
  const state = model ? summaryState(model, serverNow) : null
  const depthOf = useMemo(() => depthLookup(backfill.data), [backfill.data])

  // Rate samples for the time left: Δ rows_written over the last 2 minutes of polls.
  const samples = useRef<Sample[]>([])
  const [eta, setEta] = useState<number | null>(null)
  useEffect(() => {
    if (!model || !backfill.dataUpdatedAt) return
    const at = backfill.dataUpdatedAt
    const list = samples.current
    if (list.length === 0 || list[list.length - 1]!.at !== at) list.push({ at, written: model.written })
    while (list.length > 0 && at - list[0]!.at > 150_000) list.shift()
    setEta(etaSeconds(list, model.expected - model.written))
  }, [model, backfill.dataUpdatedAt])

  // The one orchestrated moment: the last job finishing while the page is open.
  const prevState = useRef(state)
  const [celebrate, setCelebrate] = useState(false)
  useEffect(() => {
    if (prevState.current && prevState.current !== 'done' && prevState.current !== 'notStarted' && state === 'done') {
      setCelebrate(true)
      const id = window.setTimeout(() => setCelebrate(false), 1_800)
      prevState.current = state
      return () => window.clearTimeout(id)
    }
    prevState.current = state
  }, [state])

  const [busy, setBusy] = useState<BusyKind | null>(null)
  const [notice, setNotice] = useState<Exclude<ActionAnswer, 'ok'> | null>(null)
  const [rowBusy, setRowBusy] = useState<ReadonlySet<string>>(new Set())
  const [filter, setFilter] = useState<InstFilter>('all')
  const [query, setQuery] = useState('')
  const [sheetId, setSheetId] = useState<string | null>(null)
  const instrumentsRef = useRef<HTMLElement | null>(null)

  const run = async (kind: BusyKind) => {
    setBusy(kind)
    setNotice(null)
    try {
      await action.mutateAsync({ kind: kind === 'pause' ? 'pause' : 'resync' })
      setPollUntil(Date.now() + AFTER_ACTION_MS)
      if (kind === 'pause') toast({ tone: 'info', title: t('toast.paused'), body: t('toast.pausedBody') })
      else if (kind === 'resume') toast({ title: t('toast.resumed') })
      else if (kind === 'start') toast({ title: t('toast.started'), body: t('toast.startedBody') })
      else toast({ title: t('toast.resync'), body: t('toast.resyncBody') })
    } catch (err) {
      setNotice(answerOf(err))
    } finally {
      setBusy(null)
    }
  }

  const resyncOne = async (instId: string, failed: boolean) => {
    if (rowBusy.has(instId)) return
    setRowBusy((s) => new Set(s).add(instId))
    try {
      await action.mutateAsync({ kind: 'resync', instId })
      setPollUntil(Date.now() + AFTER_ACTION_MS)
      setSheetId(null)
      toast({ title: t(failed ? 'toast.retryOne' : 'toast.resyncOne', { inst: instId }) })
    } catch (err) {
      setSheetId(null)
      if (answerOf(err) === 'limited') toast({ tone: 'warning', title: t('toast.limited') })
      else toast({ tone: 'error', title: t('down.title'), body: t('down.plain') })
    } finally {
      setRowBusy((s) => {
        const next = new Set(s)
        next.delete(instId)
        return next
      })
    }
  }

  const showFilter = (f: InstFilter) => {
    setFilter(f)
    setQuery('')
    requestAnimationFrame(() => {
      const sec = instrumentsRef.current
      if (!sec) return
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      sec.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
      sec.querySelector<HTMLButtonElement>('.dt-inst-btn')?.focus({ preventScroll: true })
    })
  }

  let body
  if (backfill.isPending) body = <Loading />
  else if (backfill.isError && !backfill.data) body = <LoadError onRetry={() => void backfill.refetch()} />
  else if (!model || state === 'notStarted' || state === null)
    body = <NotStarted busy={busy === 'start'} notice={notice} onStart={() => void run('start')} />
  else
    body = (
      <>
        <Summary
          model={model}
          state={state}
          nowMs={serverNow}
          updatedAt={backfill.dataUpdatedAt}
          etaSec={state === 'running' ? eta : null}
          busy={busy}
          notice={notice}
          celebrate={celebrate}
          storageTotal={storage.data?.total_bytes ?? null}
          bytes={bytesText}
          onAction={(k) => void run(k)}
          onFilter={showFilter}
          onOpen={setSheetId}
        />
        <Quality issues={model.issues} nowMs={nowMs} />
        <Keeps model={model} storage={storage.data} depthOf={depthOf} />
        <Instruments
          model={model}
          filter={filter}
          onFilter={setFilter}
          query={query}
          onQuery={setQuery}
          rowBusy={rowBusy}
          onOpen={setSheetId}
          onResync={(id, failed) => void resyncOne(id, failed)}
          depthOf={depthOf}
          sectionRef={instrumentsRef}
        />
        <InstrumentSheet
          inst={sheetId ? (model.byId.get(sheetId) ?? null) : null}
          model={model}
          nowMs={nowMs}
          busy={sheetId !== null && rowBusy.has(sheetId)}
          onClose={() => setSheetId(null)}
          onResync={(id, failed) => void resyncOne(id, failed)}
          depthOf={depthOf}
        />
      </>
    )

  return (
    <Tooltip.Provider delayDuration={450}>
      <div className="dt-page" data-testid="data-page">
        <Pane />
        {body}
      </div>
    </Tooltip.Provider>
  )
}
