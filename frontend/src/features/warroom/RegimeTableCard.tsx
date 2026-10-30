import { ChevronDown, ChevronUp, ChevronsUpDown, Layers } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { REGIME_ORDER, type RegimeRow, type RegimeTableCard as Card } from './historian'
import { AsOf, Fig, RegimeLabel, RuleBased } from './HistoryVerdictCard'
import { fillSlots, probability, slot, useSpanText } from './historianFormat'
import { InstrumentId } from './text'

/*
 * Regime table card (design v7 "Historian cards and live reasoning" §7): the Historian's 07:30
 * table, in the card's row order (24-hour volume rank). Filter chips per label, sortable
 * columns (Probability and In regime sort descending first; rows with no value always go last;
 * ties fall back to rank), ten rows until "Show all".
 */

type Col = 'rank' | 'inst' | 'regime' | 'prob' | 'days'
type Dir = 'asc' | 'desc'

const LIMIT = 10
const FIRST_DIR: Record<Col, Dir> = { rank: 'asc', inst: 'asc', regime: 'asc', prob: 'desc', days: 'desc' }

const regimeIndex = (label: string) => {
  const i = (REGIME_ORDER as readonly string[]).indexOf(label)
  return i < 0 ? REGIME_ORDER.length : i
}

const KEY: Record<Col, (r: RegimeRow) => number | string | null> = {
  rank: (r) => r.rank,
  inst: (r) => r.instId,
  regime: (r) => regimeIndex(r.label),
  prob: (r) => r.probability,
  days: (r) => r.days,
}

function sortRows(rows: RegimeRow[], col: Col, dir: Dir): RegimeRow[] {
  const key = KEY[col]
  return [...rows].sort((a, b) => {
    const va = key(a)
    const vb = key(b)
    if (va === null) return vb === null ? a.rank - b.rank : 1
    if (vb === null) return -1
    const d = typeof va === 'string' && typeof vb === 'string' ? va.localeCompare(vb) : (va as number) - (vb as number)
    return (dir === 'asc' ? d : -d) || a.rank - b.rank
  })
}

export function RegimeTableCard({ card, lang }: { card: Card; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const spanText = useSpanText()
  const [sort, setSort] = useState<{ col: Col; dir: Dir }>({ col: 'rank', dir: 'asc' })
  const [filter, setFilter] = useState<string>('all')
  const [all, setAll] = useState(false)

  const counts = new Map<string, number>()
  for (const r of card.rows) counts.set(r.label, (counts.get(r.label) ?? 0) + 1)
  const labels = [...REGIME_ORDER.filter((l) => counts.has(l)), ...[...counts.keys()].filter((l) => !(REGIME_ORDER as readonly string[]).includes(l))]

  const list = sortRows(filter === 'all' ? card.rows : card.rows.filter((r) => r.label === filter), sort.col, sort.dir)
  const shown = all ? list : list.slice(0, LIMIT)

  const onSort = (col: Col) => setSort((s) => (s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: FIRST_DIR[col] }))

  const th = (col: Col, label: string, cls: string, aria?: string) => {
    const on = sort.col === col
    const Icon = on ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
    return (
      <th scope="col" className={cls} aria-sort={on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
        <button type="button" className={on ? 'rt-sort is-on' : 'rt-sort'} onClick={() => onSort(col)} aria-label={aria} data-testid={`rt-sort-${col}`}>
          <span>{label}</span>
          <Icon aria-hidden="true" strokeWidth={2} className="rt-sort-ic" />
        </button>
      </th>
    )
  }

  const chip = (value: string, label: ReactNode, n: number) => (
    <button key={value} type="button" className="rt-chip" aria-pressed={filter === value} onClick={() => setFilter(value)} data-testid={`rt-chip-${value}`}>
      <span>{label}</span>
      <span className="rt-chip-n">
        <Fig>{formatFixed(n, 0, lang)}</Fig>
      </span>
    </button>
  )

  return (
    <div className="hv-card rt-card" role="group" aria-label={t('rt.title')} data-testid="rt-card">
      <div className="hv-head">
        <span className="hv-tile">
          <Layers aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{t('kind.research_report')}</span>
        {card.dataAsOf && <AsOf ts={card.dataAsOf} lang={lang} />}
      </div>
      <p className="hv-q rt-title">
        <span>{t('rt.title')}</span>
        <span className="rt-sub">{t('rt.sub', { count: card.rows.length })}</span>
      </p>
      <div className="rt-chips" role="group" aria-label={t('rt.filter')}>
        {chip('all', t('rt.all'), card.rows.length)}
        {labels.map((l) => chip(l, <RegimeLabel label={l} />, counts.get(l) ?? 0))}
      </div>
      <div className="rt-scroll">
        <table className="rt-table" data-testid="rt-table">
          <caption className="sr-only">{t('rt.caption')}</caption>
          <thead>
            <tr>
              {th('rank', '#', 'rt-rank', t('rt.col.rank'))}
              {th('inst', t('rt.col.inst'), 'rt-inst')}
              {th('regime', t('rt.col.regime'), 'rt-rg')}
              {th('prob', t('rt.col.prob'), 'rt-prob')}
              {th('days', t('rt.col.days'), 'rt-days')}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={`${r.rank}-${r.instId}`} data-testid="rt-row" data-inst={r.instId}>
                <td className="rt-rank">
                  <Fig>{formatFixed(r.rank, 0, lang)}</Fig>
                </td>
                <th scope="row" className="rt-inst">
                  <InstrumentId id={r.instId} />
                </th>
                <td className="rt-rg">
                  <RegimeLabel label={r.label} />
                </td>
                <td className="rt-prob" data-testid="rt-prob">
                  {r.probability === null ? (
                    r.days === null ? (
                      <span className="hv-none">—</span>
                    ) : (
                      <RuleBased />
                    )
                  ) : (
                    <>
                      <span className="rt-meter" aria-hidden="true">
                        <i style={{ inlineSize: `${Math.round(r.probability * 100)}%` }} />
                      </span>
                      <Fig>{probability(r.probability, lang)}</Fig>
                    </>
                  )}
                </td>
                <td className="rt-days">
                  {r.days === null ? (
                    <span className="hv-none">—</span>
                  ) : (
                    <>
                      {r.days < 1 && <span className="rt-new" role="img" tabIndex={0} aria-label={t('rg.changed')} title={t('rg.changed')} />}
                      <span className="num">{spanText(r.days)}</span>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.length > LIMIT && (
        <button type="button" className="rt-more" aria-expanded={all} onClick={() => setAll((v) => !v)} data-testid="rt-more">
          <span>{all ? t('rt.showFewer') : fillSlots(t('rt.showAll', { n: slot('n') }), { n: <Fig>{formatFixed(list.length, 0, lang)}</Fig> })}</span>
          {all ? <ChevronUp aria-hidden="true" strokeWidth={2} /> : <ChevronDown aria-hidden="true" strokeWidth={2} />}
        </button>
      )}
    </div>
  )
}
