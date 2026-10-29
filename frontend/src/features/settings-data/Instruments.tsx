import { useVirtualizer } from '@tanstack/react-virtual'
import { CircleAlert, Pause, RefreshCw, Search, SearchX, X } from 'lucide-react'
import { memo, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, SegmentedControl, Spinner } from '../../ui'
import type { ApiJob } from './api'
import { compact, DATASETS, exact, filterInsts, jobFraction, jobStatus, pctText, type DatasetDef, type DatasetKey, type Inst, type InstFilter, type Model } from './model'
import { Fig, InstId, NS, StatusPill, Tip, useDepthText } from './parts'

/* The instrument table (design §5.6): one row per instrument, one cell per job, virtualised against
   the page scroller (main#content) with fixed 56px rows and roving focus. */

export const ROW_H = 56

const JobCell = memo(function JobCell({ job, def }: { job: ApiJob | undefined; def: DatasetDef }) {
  const { t } = useTranslation(NS)
  if (!job)
    return (
      <td className="dt-cell is-none" data-ds={def.key} data-status="none">
        <span className="dt-cell-val">
          <span aria-hidden="true">—</span>
          <span className="sr-only">{t('st.none')}</span>
        </span>
      </td>
    )
  const st = jobStatus(job)
  let val: string
  let sr: string
  let meter: string | null = null
  let p = 0
  if (def.open) {
    val = job.rows_written ? compact(job.rows_written) : '—'
    sr = `${t('recordsPlain', { count: job.rows_written, n: exact(job.rows_written) })}, ${t(`st.${st}`)}`
    if (st !== 'done') meter = st === 'fetching' ? 'is-indeterminate' : st === 'failed' ? 'is-failed' : st === 'paused' ? 'is-paused' : 'is-waiting'
  } else {
    p = jobFraction(job) ?? 0
    val = pctText(p)
    sr = `${val}, ${t(`st.${st}`)}`
    if (st !== 'done') meter = st === 'failed' ? 'is-failed' : st === 'paused' ? 'is-paused' : st === 'waiting' && p === 0 ? 'is-waiting' : ''
  }
  return (
    <td className={`dt-cell is-${st}`} data-ds={def.key} data-status={st}>
      <span className="dt-cell-val">
        <span aria-hidden="true" className="contents">
          {st === 'failed' && <CircleAlert strokeWidth={2} />}
          {st === 'paused' && <Pause strokeWidth={2} />}
          <Fig>{val}</Fig>
        </span>
        <span className="sr-only">{sr}</span>
      </span>
      {meter !== null && (
        <span className={`dt-meter ${meter}`.trim()}>
          <i style={{ '--p': def.open ? 0 : p.toFixed(4) } as CSSProperties} />
        </span>
      )}
    </td>
  )
})

interface RowProps {
  inst: Inst
  focusable: boolean
  busy: boolean
  onOpen: (id: string) => void
  onResync: (id: string, failed: boolean) => void
  onFocusRow: (id: string) => void
}

const Row = memo(function Row({ inst, focusable, busy, onOpen, onResync, onFocusRow }: RowProps) {
  const { t } = useTranslation(NS)
  const ti = focusable ? 0 : -1
  const failed = inst.status === 'failed'
  return (
    <tr data-testid="dt-row" data-inst={inst.instId} data-status={inst.status} style={{ height: ROW_H }}>
      <td>
        <button
          type="button"
          className="dt-inst-btn"
          data-inst-btn={inst.instId}
          tabIndex={ti}
          aria-label={t('i.open', { inst: inst.instId })}
          onFocus={() => onFocusRow(inst.instId)}
          onClick={() => onOpen(inst.instId)}
        >
          <InstId id={inst.instId} />
          <span className="inst-cls">{t(`class1.${inst.assetClass}`)}</span>
        </button>
      </td>
      {DATASETS.map((d) => (
        <JobCell key={d.key} job={inst.jobs[d.key]} def={d} />
      ))}
      <td data-col="total">
        <span className="dt-total">
          <span className="dt-total-pct">
            <Fig>{pctText(inst.pct)}</Fig>
          </span>
          <StatusPill status={inst.status} />
        </span>
      </td>
      <td className="dt-act">
        {failed ? (
          <Button size="sm" variant="secondary" tabIndex={ti} loading={busy} aria-label={t('i.retryLabel', { inst: inst.instId })} onClick={() => onResync(inst.instId, true)}>
            {t('i.retry')}
          </Button>
        ) : (
          <Tip label={t('i.resync', { inst: inst.instId })}>
            <button
              type="button"
              className="dt-icon-btn"
              tabIndex={ti}
              aria-busy={busy || undefined}
              aria-label={t('i.resync', { inst: inst.instId })}
              data-testid="dt-row-resync"
              onClick={() => {
                if (!busy) onResync(inst.instId, false)
              }}
            >
              {busy ? <Spinner /> : <RefreshCw aria-hidden="true" strokeWidth={1.75} />}
            </button>
          </Tip>
        )}
      </td>
    </tr>
  )
})

export interface InstrumentsProps {
  model: Model
  filter: InstFilter
  onFilter: (f: InstFilter) => void
  query: string
  onQuery: (q: string) => void
  rowBusy: ReadonlySet<string>
  onOpen: (id: string) => void
  onResync: (id: string, failed: boolean) => void
  depthOf: (k: DatasetKey) => number | null
  sectionRef: RefObject<HTMLElement | null>
}

const scroller = () => document.getElementById('content')

export function Instruments(p: InstrumentsProps) {
  const { t } = useTranslation(NS)
  const depthText = useDepthText()
  const list = filterInsts(p.model.insts, p.filter, p.query)
  const [activeId, setActiveId] = useState<string | null>(null)
  const active = activeId !== null && list.some((i) => i.instId === activeId) ? activeId : (list[0]?.instId ?? null)
  const tbodyRef = useRef<HTMLTableSectionElement>(null)
  const [margin, setMargin] = useState(0)

  useLayoutEffect(() => {
    const measure = () => {
      const el = tbodyRef.current
      const sc = scroller()
      if (!el || !sc) return
      const m = el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop
      setMargin((prev) => (Math.abs(prev - m) > 0.5 ? m : prev))
    }
    measure()
    const ro = new ResizeObserver(measure)
    const page = tbodyRef.current?.closest('.dt-page')
    if (page) ro.observe(page)
    return () => ro.disconnect()
  }, [list.length])

  const virtualizer = useVirtualizer({
    count: list.length,
    getScrollElement: scroller,
    estimateSize: () => ROW_H,
    overscan: 8,
    scrollMargin: margin,
    initialRect: { width: 1200, height: 900 },
    getItemKey: (index) => list[index]!.instId,
  })
  const items = virtualizer.getVirtualItems()
  const padTop = items.length > 0 ? items[0]!.start - margin : 0
  const padBottom = items.length > 0 ? virtualizer.getTotalSize() - (items[items.length - 1]!.end - margin) : 0

  const focusRow = (id: string) => {
    const tryFocus = (left: number) => {
      const btn = tbodyRef.current?.querySelector<HTMLButtonElement>(`[data-inst-btn="${CSS.escape(id)}"]`)
      if (btn) btn.focus({ preventScroll: true })
      else if (left > 0) requestAnimationFrame(() => tryFocus(left - 1))
    }
    tryFocus(4)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLTableSectionElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    if (!(e.target instanceof HTMLElement) || !e.target.closest('.dt-inst-btn')) return
    const k = list.findIndex((i) => i.instId === active)
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? list.length - 1 : Math.max(0, Math.min(list.length - 1, k + (e.key === 'ArrowDown' ? 1 : -1)))
    if (next === k || !list[next]) return
    e.preventDefault()
    const id = list[next].instId
    setActiveId(id)
    virtualizer.scrollToIndex(next, { align: 'auto' })
    focusRow(id)
  }

  const q = p.query.trim()
  return (
    <section className="dt-section" id="dt-instruments" aria-labelledby="dt-i-title" ref={p.sectionRef} data-testid="dt-instruments">
      <div className="dt-sec-head">
        <h2 className="dt-group-title" id="dt-i-title">
          {t('i.title')}
          <Fig className="num">{p.model.insts.length}</Fig>
        </h2>
        <div className="dt-tools">
          <label className="dt-search">
            <Search aria-hidden="true" strokeWidth={1.75} />
            <span className="sr-only">{t('i.search')}</span>
            <input
              className="dt-input"
              type="search"
              placeholder={t('i.search')}
              value={p.query}
              autoComplete="off"
              spellCheck={false}
              dir="auto"
              data-testid="dt-search"
              onChange={(e) => p.onQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && p.query) {
                  e.stopPropagation()
                  p.onQuery('')
                }
              }}
            />
            {p.query && (
              <button type="button" className="dt-search-clear" aria-label={t('i.clear')} onClick={() => p.onQuery('')}>
                <X aria-hidden="true" />
              </button>
            )}
          </label>
          <SegmentedControl
            aria-label={t('i.filterLabel')}
            value={p.filter}
            onValueChange={p.onFilter}
            options={[
              { value: 'all', label: t('i.all') },
              { value: 'progress', label: t('i.progress') },
              { value: 'failed', label: t('i.failed') },
              { value: 'done', label: t('i.done') },
            ]}
          />
        </div>
      </div>
      <div className="dt-panel dt-matrix-panel">
        <table className="dt-matrix" data-testid="dt-table">
          <colgroup>
            <col className="c-inst" />
            {DATASETS.map((d) => (
              <col key={d.key} className="c-ds" />
            ))}
            <col className="c-total" />
            <col className="c-act" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">{t('i.col')}</th>
              {DATASETS.map((d) => (
                <th key={d.key} scope="col" className="is-num" title={`${t(`ds.${d.key}`)}, ${depthText(d.key, p.depthOf(d.key))}`}>
                  <span aria-hidden="true">{d.code ? <Fig>{d.code}</Fig> : t(`col.${d.key}`)}</span>
                  <span className="sr-only">{t(`ds.${d.key}`)}</span>
                </th>
              ))}
              <th scope="col" className="is-num">
                {t('i.total')}
              </th>
              <th scope="col">
                <span className="sr-only">{t('i.actions')}</span>
              </th>
            </tr>
          </thead>
          <tbody ref={tbodyRef} onKeyDown={onKeyDown}>
            {list.length === 0 ? (
              <tr className="dt-noresults">
                <td colSpan={11}>
                  <div className="dt-state-block" role="status">
                    <span className="dt-state-icon" aria-hidden="true">
                      <SearchX strokeWidth={1.5} />
                    </span>
                    <h3 className="text-headline">{q ? t('i.none', { q }) : t('i.noneFilter')}</h3>
                    <p className="text-subheadline text-label-secondary">{q ? t('i.noneBody') : t('i.noneFilterBody')}</p>
                  </div>
                </td>
              </tr>
            ) : (
              <>
                {padTop > 0 && <tr aria-hidden="true" style={{ height: padTop }} />}
                {items.map((v) => {
                  const inst = list[v.index]!
                  return (
                    <Row
                      key={inst.instId}
                      inst={inst}
                      focusable={inst.instId === active}
                      busy={p.rowBusy.has(inst.instId)}
                      onOpen={p.onOpen}
                      onResync={p.onResync}
                      onFocusRow={setActiveId}
                    />
                  )
                })}
                {padBottom > 0 && <tr aria-hidden="true" style={{ height: padBottom }} />}
              </>
            )}
          </tbody>
        </table>
      </div>
      <p className="dt-table-note">{t('i.order')}</p>
    </section>
  )
}
