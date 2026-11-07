import { Download, History, SearchX } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Button, SegmentedControl, toast } from '../../ui'
import { POLL_MS, useSummary } from './api'
import { HistoryFilterBar } from './HistoryFilters'
import {
  BLANK_FILTERS,
  downloadExport,
  exportUrl,
  isFiltered,
  useHistory,
  type ClosedRow,
  type FillRow,
  type HistoryFilters,
  type HistoryType,
} from './historyApi'
import { HistoryTable, ROW_H } from './HistoryTable'
import { useLang, useNow } from './hooks'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import { baghdadDay } from './time'
import './portfolio.css'

/*
 * Portfolio → History (brief §13, design v14 #/portfolio/history, ATL-173). Fills and closed
 * positions from ATL-172's GET /api/portfolio/history, one filter row (dates in Baghdad,
 * instrument, asset class, agent, strategy, side, result) with removable chips, a virtualised
 * table (HistoryTable) and Export CSV of exactly what the filters ask for.
 */

export function HistoryPage() {
  const { t } = useTranslation(['portfolio-history', 'warroom'])
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(60_000)
  const reconNow = useNow(POLL_MS)
  const summary = useSummary(env)
  // A notification's `?inst_id=` (ATL-220, design v18 §5.6) lands on Closed positions, pre-filtered
  // to that instrument, then drops the query: a reload starts over blank, like every other filter here.
  const [params, setParams] = useSearchParams()
  const linkedInst = useRef(params.get('inst_id')).current
  const [type, setType] = useState<HistoryType>(linkedInst ? 'closed' : 'fills')
  const [f, setF] = useState<HistoryFilters>(() => (linkedInst ? { ...BLANK_FILTERS, inst: linkedInst.trim().toUpperCase() } : BLANK_FILTERS))
  const [exporting, setExporting] = useState(false)
  const history = useHistory<ClosedRow | FillRow>(type, f, env, now)

  useEffect(() => {
    if (!params.has('inst_id')) return
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        next.delete('inst_id')
        return next
      },
      { replace: true },
    )
    // Reads `inst_id` once, on arrival; it must not re-run as `params` itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const rows = useMemo(() => history.data?.pages.flatMap((p) => p.data) ?? [], [history.data])
  const onNearEnd = useCallback(() => {
    void history.fetchNextPage()
  }, [history])

  const onExport = async () => {
    setExporting(true)
    try {
      const { rows: n, file } = await downloadExport(exportUrl(type, f, env, now), `atlas-${env}-${type}-${baghdadDay(Date.now())}.csv`)
      toast({
        title: t(`exported.${type}`, { count: n }),
        body: t('exportedBody', { file }),
      })
    } catch {
      toast({
        tone: 'error',
        title: t('exportFailed'),
        body: t('exportFailedBody'),
      })
    } finally {
      setExporting(false)
    }
  }

  const filtered = isFiltered(type, f)
  const loading = history.isPending
  const s = summary.data?.data

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-4 pt-6 pb-4">
      <SegmentedControl<HistoryType>
        aria-label={t('tabs')}
        value={type}
        onValueChange={setType}
        options={[
          { value: 'fills', label: t('tab.fills') },
          { value: 'closed', label: t('tab.closed') },
        ]}
      />
      <div className="flex flex-wrap items-center justify-end gap-4">
        {s ? <ReconciledIndicator summary={s} lang={lang} now={reconNow} /> : <Skeleton style={{ width: 250, height: 26 }} />}
        <Button
          size="sm"
          variant="secondary"
          icon={<Download aria-hidden="true" strokeWidth={1.75} />}
          loading={exporting}
          disabled={loading || rows.length === 0}
          onClick={() => void onExport()}
          data-testid="export-csv"
        >
          {t('export')}
        </Button>
      </div>
    </header>
  )

  let body: ReactNode
  if (history.isError && !history.data) {
    body = (
      <div className="pf-panel">
        <StateBlock kind="error" compact title={t('error.title')} body={t('error.body')} onRetry={() => void history.refetch()} />
      </div>
    )
  } else if (loading) {
    body = (
      <div className="pf-panel" data-testid="history-loading">
        <div className="grid gap-0">
          {Array.from({ length: 9 }, (_, i) => (
            <div key={i} className="flex items-center gap-6 border-b-[0.5px] border-separator px-5" style={{ height: ROW_H }}>
              <Skeleton style={{ width: 110, height: 12 }} />
              <Skeleton style={{ width: 120, height: 12 }} />
              <Skeleton style={{ width: 54, height: 12 }} />
              <Skeleton style={{ width: 64, height: 12 }} />
              <Skeleton style={{ width: 64, height: 12 }} />
            </div>
          ))}
        </div>
      </div>
    )
  } else if (rows.length === 0) {
    body = filtered ? (
      <div className="pf-panel grid justify-items-center pb-10">
        <StateBlock kind="none" compact icon={SearchX} title={t('empty.title')} body={t('empty.body')} />
        <Button variant="secondary" onClick={() => setF(BLANK_FILTERS)} className="-mt-10">
          {t('f.clear')}
        </Button>
      </div>
    ) : (
      <div className="pf-panel">
        <StateBlock kind="none" compact icon={History} title={t('none.title')} body={type === 'fills' ? t('none.fillsBody') : t('none.body')} />
      </div>
    )
  } else {
    body = (
      <>
        <HistoryTable
          type={type}
          rows={rows}
          lang={lang}
          hasMore={history.hasNextPage}
          loadingMore={history.isFetchingNextPage}
          onNearEnd={onNearEnd}
        />
        <p className="ph-foot">{t('foot')}</p>
      </>
    )
  }

  return (
    <div className="ph-page" aria-busy={loading || undefined} data-testid="history-page" data-type={type}>
      {loading && (
        <span className="sr-only" role="status">
          {t('loading')}
        </span>
      )}
      {head}
      <HistoryFilterBar type={type} f={f} onChange={setF} now={now} />
      {body}
    </div>
  )
}
