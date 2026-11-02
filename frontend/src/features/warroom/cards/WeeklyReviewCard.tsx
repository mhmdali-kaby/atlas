import { CalendarRange, FileText, Info, Lightbulb, Settings } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed, signOf, type Lang } from '../../../lib/format'
import { Button, SegmentedControl } from '../../../ui'
import { fillSlots, slot } from '../slots'
import { Inline } from '../text'
import { formatDate } from '../time'
import { useTagLabel, type ReviewGroup, type WeeklyReview } from './learning'
import { Fig, Signed } from './pmParts'
import { SignedR } from './PostMortemCard'
import { AsOf } from './TechnicalSetupCard'
import './learning.css'

/*
 * The Historian's weekly review in the War Room (brief §7.3 "Weekly (Sunday 18:00 Baghdad)",
 * design v16 §9, ATL-203; card JSON from ATL-199). Every figure is code's: the verdict line, a
 * By control (Setup, Market, Agent) over the grouped table ending in "All trades", the paper-trade
 * line when ATL-196's table exists, and exactly one suggested change. A week with no closed trades
 * is a short code-only block. A failed model run (status degraded, error, …) keeps the figures and
 * says there is no verdict.
 */

type By = 'setup' | 'class' | 'agent'

const WHERE_PATH = { risk: '/settings/risk', markets: '/settings/markets', money: '/settings/money' } as const

/** The window's first and last Baghdad days ("19 Oct to 25 Oct"). */
function spanOf(w: WeeklyReview, lang: Lang) {
  return { from: formatDate(w.from, lang), to: formatDate(w.to, lang) }
}

function GroupRow({ g, name, total = false, lang }: { g: ReviewGroup; name: string; total?: boolean; lang: Lang }) {
  return (
    <tr className={total ? 'is-total' : undefined} data-testid={total ? 'wk-total' : 'wk-row'} data-key={g.key}>
      <th scope="row" dir="auto">
        {name}
      </th>
      <td>
        <Fig>{formatFixed(String(g.count), 0, lang)}</Fig>
      </td>
      <td>
        <Fig>{formatFixed(String(g.wins), 0, lang)}</Fig>
      </td>
      <td>
        <SignedR value={g.totalR} />
      </td>
      <td>{g.totalUsd === null ? '—' : <Signed value={g.totalUsd} lang={lang} />}</td>
    </tr>
  )
}

export function WeeklyReviewCard({ review: w, lang }: { review: WeeklyReview; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const tagLabel = useTagLabel()
  const [by, setBy] = useState<By>('setup')
  const span = spanOf(w, lang)

  const head = (
    <div className="hv-head lc-head">
      <span className="hv-tile">
        <CalendarRange aria-hidden="true" strokeWidth={1.5} />
      </span>
      <span className="hv-kind">{t('wk.kind')}</span>
      <span className="lc-ref" data-testid="wk-span">
        {t('wk.span', span)}
      </span>
      <AsOf ts={w.asOf} lang={lang} testId="wk-asof" />
    </div>
  )

  if (w.noTrades) {
    return (
      <div className="hv-card lc-card wk-card" role="group" aria-label={t('wk.kind')} data-testid="wk-card">
        {head}
        <div className="wk-quiet" data-testid="wk-quiet">
          <CalendarRange className="icon" aria-hidden="true" />
          <div>
            <p className="wk-quiet-t">{t('wk.none')}</p>
            <p className="wk-quiet-b">{t('wk.noneBody', span)}</p>
          </div>
        </div>
      </div>
    )
  }

  const groups = by === 'setup' ? w.bySetup : by === 'class' ? w.byAssetClass : w.byAgent
  const nameOf = (key: string) => {
    if (key === 'unknown') return t('wk.unknown')
    if (by === 'setup') return tagLabel(`setup:${key}`)
    if (by === 'class') return tagLabel(`asset:${key}`)
    const k = `agents.${key}.name`
    const n = t(k)
    return n === k ? key : n
  }

  let paper = null
  if (w.paper && w.paper.filled > 0 && w.paper.totalR !== null) {
    const s = signOf(w.paper.totalR)
    const ideas = t('wk.ideas', { count: w.paper.filled })
    const abs = w.paper.totalR.replace(/^-/, '')
    paper = (
      <p className="wk-paper" data-testid="wk-paper">
        <FileText className="icon" aria-hidden="true" />
        <span>
          {s === 0
            ? t('wk.paperFlat', { ideas })
            : fillSlots(t(s === -1 ? 'wk.paperLost' : 'wk.paperMade', { ideas, r: slot('r') }), { r: <Fig>{`${formatFixed(abs, 1, lang)}R`}</Fig> })}
        </span>
      </p>
    )
  }

  return (
    <div className="hv-card lc-card wk-card" role="group" aria-label={t('wk.kind')} data-testid="wk-card">
      {head}
      {w.verdict ? (
        <p className="wk-verdict" dir="auto" data-testid="wk-verdict">
          <Inline text={w.verdict} flat />
        </p>
      ) : (
        <p className="wk-noverdict" data-testid="wk-noverdict">
          <Info className="icon" aria-hidden="true" />
          <span>{t('wk.noVerdict')}</span>
        </p>
      )}

      <div className="wk-table-wrap">
        <div className="wk-by">
          <SegmentedControl<By>
            aria-label={t('wk.by')}
            value={by}
            onValueChange={setBy}
            options={[
              { value: 'setup', label: t('wk.bySetup') },
              { value: 'class', label: t('wk.byClass') },
              { value: 'agent', label: t('wk.byAgent') },
            ]}
          />
        </div>
        <table className="wk-table" data-by={by} data-testid="wk-table">
          <thead>
            <tr>
              <th scope="col">{t('wk.col.group')}</th>
              <th scope="col">{t('wk.col.trades')}</th>
              <th scope="col">{t('wk.col.won')}</th>
              <th scope="col">{t('wk.col.r')}</th>
              <th scope="col">{t('wk.col.usd')}</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <GroupRow key={g.key} g={g} name={nameOf(g.key)} lang={lang} />
            ))}
            <GroupRow g={w.totals} name={t('wk.total')} total lang={lang} />
          </tbody>
        </table>
      </div>

      {paper}

      {w.suggestion && (
        <section className="wk-change" aria-label={t('wk.change')} data-testid="wk-change">
          <p className="wk-change-k">
            <Lightbulb className="icon" aria-hidden="true" />
            <span>{t('wk.change')}</span>
          </p>
          <p className="wk-change-text" dir="auto" data-testid="wk-change-text">
            <Inline text={w.suggestion.change} flat />
          </p>
          <div className="wk-change-foot">
            {w.suggestion.where && (
              <Button asChild size="sm" variant="secondary">
                <Link to={WHERE_PATH[w.suggestion.where]} data-testid="wk-change-link">
                  <Settings aria-hidden="true" strokeWidth={1.75} />
                  <span>{t('wk.open', { where: t(`wk.where.${w.suggestion.where}`) })}</span>
                </Link>
              </Button>
            )}
            <span className="wk-change-note">{t('wk.changeNote')}</span>
          </div>
        </section>
      )}
    </div>
  )
}

