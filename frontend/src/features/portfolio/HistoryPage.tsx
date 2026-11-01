import { Check, ChevronDown, Download, History, Search, SearchX, X } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { useCallback, useId, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Button, SegmentedControl, Sheet, SheetClose, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { useDir } from '../../ui/useDir'
import { useInstrumentSearch } from '../warroom/watchlist'
import { POLL_MS, useSummary } from './api'
import {
  ASSET_CLASSES,
  BLANK_FILTERS,
  baghdadDay,
  downloadExport,
  exportUrl,
  HISTORY_AGENTS,
  RANGE_PRESETS,
  STRATEGY_TAGS,
  useHistory,
  type ClosedRow,
  type FillRow,
  type HistoryFilters,
  type HistoryType,
  type RangePreset,
} from './historyApi'
import { HistoryTable, ROW_H } from './HistoryTable'
import { useLang, useNow } from './hooks'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import { formatDay } from './time'
import './portfolio.css'

/*
 * Portfolio → History (brief §13, design v14 #/portfolio/history, ATL-173). Fills and closed
 * positions from ATL-172's GET /api/portfolio/history, one filter row (dates in Baghdad,
 * instrument, asset class, agent, strategy, side, result) with removable chips, a virtualised
 * table (HistoryTable) and Export CSV of exactly what the filters ask for.
 */

const menuItem =
  'group flex min-h-[30px] w-full cursor-default items-center gap-2.5 rounded-[7px] px-2.5 py-1 text-start text-subheadline text-label outline-none select-none data-highlighted:bg-accent-solid data-highlighted:text-on-solid'

interface MenuOption {
  value: string
  label: string
}

/** A filter button that opens a radio menu (design: .ph-menu, accent wash while set). */
function FilterMenu({
  label,
  heading,
  value,
  allOption,
  options,
  onChange,
  disabled,
  title,
  testId,
  extra,
}: {
  label: string
  heading: string
  value: string
  allOption?: MenuOption
  options: MenuOption[]
  onChange: (v: string) => void
  disabled?: boolean
  title?: string
  testId: string
  extra?: ReactNode
}) {
  const dir = useDir()
  const set = allOption ? value !== allOption.value : value !== 'all'
  const item = (o: MenuOption) => (
    <DropdownMenu.RadioItem key={o.value} value={o.value} className={menuItem} data-value={o.value}>
      <span>{o.label}</span>
      <DropdownMenu.ItemIndicator className="ms-auto">
        <Check aria-hidden="true" strokeWidth={2} className="size-4 text-accent group-data-highlighted:text-on-solid" />
      </DropdownMenu.ItemIndicator>
    </DropdownMenu.RadioItem>
  )
  return (
    <DropdownMenu.Root dir={dir} modal={false}>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className={cx('ph-menu', set && 'is-set')} data-testid={testId} title={title} aria-label={`${heading}: ${label}`}>
          <span>{label}</span>
          <ChevronDown aria-hidden="true" strokeWidth={2} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={6}
          className="shell-popover z-60 max-h-[60vh] min-w-[220px] overflow-auto rounded-card p-[5px] vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
        >
          <DropdownMenu.Label className="px-2.5 pt-1.5 pb-1 text-footnote font-semibold text-label-secondary">{heading}</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={value} onValueChange={onChange}>
            {allOption && item(allOption)}
            {allOption && <DropdownMenu.Separator className="mx-2.5 my-[5px] h-[0.5px] bg-separator" />}
            {options.map(item)}
          </DropdownMenu.RadioGroup>
          {extra}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

/** Instrument search: suggestions from ATL-119's search; the filter is the exact instId (ATL-172). */
function InstrumentField({ value, onApply }: { value: string; onApply: (v: string) => void }) {
  const { t } = useTranslation('portfolio-history')
  const env = useTradingEnv()
  const [draft, setDraft] = useState(value)
  const [lastValue, setLastValue] = useState(value)
  if (value !== lastValue) {
    setLastValue(value)
    setDraft(value)
  }
  const listId = useId()
  const hits = useInstrumentSearch(env, draft.trim().length >= 2 && draft.trim().toUpperCase() !== value ? draft : '')
  const ids = hits.data?.data.map((h) => h.inst_id) ?? []
  return (
    <label className="ph-search">
      <Search aria-hidden="true" strokeWidth={2} />
      <span className="sr-only">{t('f.search')}</span>
      <input
        className="ph-search-input"
        type="search"
        data-testid="filter-inst"
        value={draft}
        list={listId}
        placeholder={t('f.searchPh')}
        title={t('f.searchHint')}
        autoComplete="off"
        spellCheck={false}
        dir="ltr"
        onChange={(e) => {
          const v = e.target.value
          setDraft(v)
          if (v.trim() === '') onApply('')
          else if (ids.includes(v.trim().toUpperCase())) onApply(v.trim().toUpperCase())
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            onApply(draft.trim().toUpperCase())
          }
        }}
        onBlur={() => {
          if (draft.trim().toUpperCase() !== value) onApply(draft.trim().toUpperCase())
        }}
      />
      <datalist id={listId}>
        {ids.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
    </label>
  )
}

/** Custom dates: two Baghdad days, inclusive, from ≤ to. */
function RangeSheet({
  open,
  onOpenChange,
  initial,
  max,
  onApply,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  initial: { from: string; to: string }
  max: string
  onApply: (from: string, to: string) => void
}) {
  const { t } = useTranslation('portfolio-history')
  const [from, setFrom] = useState(initial.from)
  const [to, setTo] = useState(initial.to)
  const [bad, setBad] = useState(false)
  const errId = useId()
  const apply = () => {
    if (!from || !to || from > to) {
      setBad(true)
      return
    }
    onApply(from, to)
  }
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={t('r.sheetTitle')}
      description={t('r.sheetText')}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary">{t('r.cancel')}</Button>
          </SheetClose>
          <Button variant="primary" onClick={apply} data-testid="range-apply">
            {t('r.apply')}
          </Button>
        </>
      }
    >
      <div className="ph-range-fields">
        {(
          [
            ['from', from, setFrom],
            ['to', to, setTo],
          ] as const
        ).map(([k, v, setV]) => (
          <label key={k} className="grid gap-1.5">
            <span className="text-footnote font-medium text-label-secondary">{t(`r.${k}`)}</span>
            <input
              className="ph-date"
              type="date"
              dir="ltr"
              value={v}
              max={max}
              data-testid={`range-${k}`}
              aria-invalid={bad || undefined}
              aria-describedby={bad ? errId : undefined}
              onChange={(e) => {
                setV(e.target.value)
                setBad(false)
              }}
            />
          </label>
        ))}
      </div>
      {bad && (
        <p id={errId} role="alert" className="mt-3 text-footnote text-loss-ink">
          {t('r.bad')}
        </p>
      )}
    </Sheet>
  )
}

export function HistoryPage() {
  const { t } = useTranslation(['portfolio-history', 'warroom'])
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(60_000)
  const reconNow = useNow(POLL_MS)
  const summary = useSummary(env)
  const [type, setType] = useState<HistoryType>('fills')
  const [f, setF] = useState<HistoryFilters>(BLANK_FILTERS)
  const [rangeOpen, setRangeOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  const history = useHistory<ClosedRow | FillRow>(type, f, env, now)

  const rows = useMemo(() => history.data?.pages.flatMap((p) => p.data) ?? [], [history.data])
  const patch = (p: Partial<HistoryFilters>) => setF((cur) => ({ ...cur, ...p }))
  const onNearEnd = useCallback(() => {
    void history.fetchNextPage()
  }, [history])

  const fmtDay = (d: string) => formatDay(`${d}T09:00:00Z`, lang)
  const rangeLabel =
    f.range === 'custom' && f.from && f.to ? t('r.span', { from: fmtDay(f.from), to: fmtDay(f.to) }) : t(`r.${f.range}`)

  const chips: Array<[keyof HistoryFilters | 'range', string]> = []
  if (f.range !== 'all') chips.push(['range', rangeLabel])
  if (f.inst) chips.push(['inst', t('f.inst', { q: f.inst })])
  if (f.assetClass !== 'all') chips.push(['assetClass', t(`cls.${f.assetClass}`)])
  if (f.agent !== 'all') chips.push(['agent', t(`warroom:agents.${f.agent}.name`)])
  if (type === 'closed' && f.tag !== 'all') chips.push(['tag', t(`tag.${f.tag}`)])
  if (f.side !== 'all') chips.push(['side', t(`f.${f.side}`)])
  if (f.result !== 'all') chips.push(['result', t(f.result === 'win' ? 'f.wins' : 'f.losses')])
  const filtered = chips.length > 0

  const clearOne = (k: keyof HistoryFilters | 'range') => {
    if (k === 'range') patch({ range: 'all', from: null, to: null })
    else patch({ [k]: BLANK_FILTERS[k] } as Partial<HistoryFilters>)
  }

  const onExport = async () => {
    setExporting(true)
    try {
      const { rows: n, file } = await downloadExport(exportUrl(type, f, env, now), `atlas-${env}-${type}-${baghdadDay(Date.now())}.csv`)
      toast({ title: t(`exported.${type}`, { count: n }), body: t('exportedBody', { file }) })
    } catch {
      toast({ tone: 'error', title: t('exportFailed'), body: t('exportFailedBody') })
    } finally {
      setExporting(false)
    }
  }

  const loading = history.isPending
  const s = summary.data?.data
  const opt = (prefix: string) => (v: string) => ({ value: v, label: t(`${prefix}.${v}`) })

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

  const filterBar = (
    <div className="ph-filters" role="group" aria-label={t('filters')} data-testid="history-filters">
      <FilterMenu
        testId="filter-range"
        heading={t('f.range')}
        label={rangeLabel}
        value={f.range}
        options={[...RANGE_PRESETS.map(opt('r')), { value: 'custom', label: t('r.custom') }]}
        onChange={(v) => {
          if (v === 'custom') setRangeOpen(true)
          else patch({ range: v as RangePreset, from: null, to: null })
        }}
      />
      <InstrumentField value={f.inst} onApply={(inst) => patch({ inst })} />
      <FilterMenu
        testId="filter-class"
        heading={t('f.cls')}
        label={f.assetClass === 'all' ? t('f.cls') : t(`cls.${f.assetClass}`)}
        value={f.assetClass}
        allOption={{ value: 'all', label: t('f.clsAll') }}
        options={ASSET_CLASSES.map(opt('cls'))}
        onChange={(assetClass) => patch({ assetClass })}
      />
      <FilterMenu
        testId="filter-agent"
        heading={t('f.agent')}
        label={f.agent === 'all' ? t('f.agent') : t(`warroom:agents.${f.agent}.name`)}
        value={f.agent}
        allOption={{ value: 'all', label: t('f.agentAll') }}
        options={HISTORY_AGENTS.map((a) => ({ value: a, label: t(`warroom:agents.${a}.name`) }))}
        onChange={(agent) => patch({ agent })}
      />
      <FilterMenu
        testId="filter-tag"
        heading={t('f.tag')}
        label={f.tag === 'all' || type === 'fills' ? t('f.tag') : t(`tag.${f.tag}`)}
        value={type === 'fills' ? 'all' : f.tag}
        allOption={{ value: 'all', label: t('f.tagAll') }}
        options={STRATEGY_TAGS.map(opt('tag'))}
        onChange={(tag) => patch({ tag })}
        disabled={type === 'fills'}
        title={type === 'fills' ? t('f.tagFillsTip') : undefined}
      />
      <SegmentedControl<HistoryFilters['side']>
        aria-label={t('f.side')}
        value={f.side}
        onValueChange={(side) => patch({ side })}
        options={[
          { value: 'all', label: t('f.all') },
          { value: 'buy', label: t('f.buy') },
          { value: 'sell', label: t('f.sell') },
        ]}
      />
      <SegmentedControl<HistoryFilters['result']>
        aria-label={t('f.result')}
        value={f.result}
        onValueChange={(result) => patch({ result })}
        options={[
          { value: 'all', label: t('f.all') },
          { value: 'win', label: t('f.wins') },
          { value: 'loss', label: t('f.losses') },
        ]}
      />
    </div>
  )

  const chipRow = filtered && (
    <div className="ph-chips" aria-live="polite" data-testid="history-chips">
      {chips.map(([k, label]) => (
        <span key={k} className="ph-chip">
          <span>{label}</span>
          <button type="button" className="ph-chip-x" aria-label={t('f.remove', { label })} onClick={() => clearOne(k)}>
            <X aria-hidden="true" />
          </button>
        </span>
      ))}
      <Button size="sm" variant="plain" onClick={() => setF(BLANK_FILTERS)}>
        {t('f.clear')}
      </Button>
    </div>
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
      {filterBar}
      {chipRow}
      {body}
      {rangeOpen && (
        <RangeSheet
          open={rangeOpen}
          onOpenChange={setRangeOpen}
          initial={{ from: f.from ?? baghdadDay(now - 6 * 86_400_000), to: f.to ?? baghdadDay(now) }}
          max={baghdadDay(now)}
          onApply={(from, to) => {
            patch({ range: 'custom', from, to })
            setRangeOpen(false)
          }}
        />
      )}
    </div>
  )
}
