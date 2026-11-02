import { ChevronLeft, CircleCheck, CircleX, Lightbulb, MessagesSquare, OctagonAlert, PenLine, ThumbsDown, ThumbsUp } from 'lucide-react'
import { forwardRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed, formatPrice, formatSigned, type Lang } from '../../lib/format'
import { Button } from '../../ui'
import { cx } from '../../ui/cx'
import { formatDuration } from '../portfolio/time'
import { Medallion } from '../warroom/Medallion'
import { fillSlots, slot } from '../warroom/slots'
import { Inline, Markdown } from '../warroom/text'
import { formatTime } from '../warroom/time'
import { rText } from '../warroom/cards/rText'
import type { JournalEntry } from './api'
import {
  closedAt,
  closeLabel,
  displayTags,
  isoMs,
  lessonPending,
  lessonText,
  miniGeometry,
  outcomeOf,
  realisedR,
  sideOf,
  tp1Hit,
  voteRight,
} from './model'
import { OUTCOME_ICON, usdText, useTagLabel, whenText } from './format'
import { Fig, Inst, MiniLadder, SignedR } from './parts'

/*
 * One post-mortem in full (design §7 "Entry (JournalEntry)"): the head with R, USD and the outcome
 * beside the verbatim close label; the lesson first; how the trade went; expected against what
 * happened; what the team got right and wrong; each voter's call; then tags, the agents named and
 * the link to the proposal's War Room message.
 */

const MIN = 60_000

/** A duration reads in the sentence's own direction in Arabic, so it is never an LTR island. */
function Dur({ ms, lang }: { ms: number; lang: Lang }) {
  const text = formatDuration(ms, lang)
  return lang === 'ar' ? <span className="num">{text}</span> : <Fig>{text}</Fig>
}

function T({ k, vars, nodes }: { k: string; vars?: Record<string, string>; nodes: Record<string, ReactNode> }) {
  const { t } = useTranslation('journal')
  const slots = Object.fromEntries(Object.keys(nodes).map((n) => [n, slot(n)]))
  return <>{fillSlots(t(k, { ...vars, ...slots }), nodes)}</>
}

function pct(v: string | null | undefined, lang: Lang): string | null {
  if (v === null || v === undefined || v === '') return null
  return `${formatFixed(String(Number(v) * 100), 0, lang)}%`
}

function CompareTable({ e, lang }: { e: JournalEntry; lang: Lang }) {
  const { t } = useTranslation('journal')
  const x = e.data.expected ?? {}
  const a = e.data.actual ?? {}
  const P = (p: string | null | undefined) => (p ? <Fig>{formatPrice(p, lang)}</Fig> : '—')
  const zone = x.entry_zone && x.entry_zone.length === 2 ? <Fig>{`${formatPrice(x.entry_zone[0], lang)}–${formatPrice(x.entry_zone[1], lang)}`}</Fig> : P(x.entry)
  const rule = closeLabel(e)
  const hit = tp1Hit(e)
  const held = a.holding_minutes ?? (a.opened_at && a.closed_at ? (isoMs(a.closed_at) - isoMs(a.opened_at)) / MIN : null)
  const tps = (x.take_profits ?? []).filter((tp) => tp.price)
  const prob = pct(x.probability, lang)
  const row = (k: string, exp: ReactNode, act: ReactNode) => ({ k, exp, act })
  const rows = [
    row('entry', zone, a.avg_entry ? <T k="c.avg" nodes={{ p: P(a.avg_entry) }} /> : '—'),
    row(
      'stop',
      x.stop ? a.initial_risk_usd ? <T k="c.stopPlan" nodes={{ p: P(x.stop), usd: <Fig>{formatSigned(a.initial_risk_usd, 'USD', lang).replace(/^\+/, '')}</Fig> }} /> : P(x.stop) : '—',
      a.close_reason === 'stop_loss' ? (
        <span className="jr-c-bad">
          <OctagonAlert aria-hidden="true" strokeWidth={2} />
          {t('c.stopHit')}
        </span>
      ) : (
        t('c.stopNot')
      ),
    ),
    row(
      'exit',
      tps.length ? (
        <span className="jr-tps">
          {tps.map((tp, i) => (
            <span key={i}>
              <T k="c.tp" vars={{ n: String(i + 1) }} nodes={{ p: P(tp.price) }} />
            </span>
          ))}
        </span>
      ) : (
        '—'
      ),
      <>
        {a.avg_exit ? <T k="c.avg" nodes={{ p: P(a.avg_exit) }} /> : '—'}
        {rule && <span className="jr-c-rule">{t(`rule.${rule}`)}</span>}
      </>,
    ),
    row(
      'result',
      x.expected_r ? <T k="c.expR" nodes={{ r: <Fig>{rText(x.expected_r, 1)}</Fig> }} /> : '—',
      <span className="jr-c-result">
        <SignedR value={realisedR(e)} />
        <Fig>{usdText(a.realised_pnl_usd, lang)}</Fig>
      </span>,
    ),
    row(
      'tp1',
      prob ? <T k="c.prob" nodes={{ p: <Fig>{prob}</Fig> }} /> : '—',
      hit === null ? (
        '—'
      ) : hit ? (
        <span className="jr-c-good">
          <CircleCheck aria-hidden="true" strokeWidth={2} />
          {t('c.yes')}
        </span>
      ) : (
        <span className="jr-c-bad">
          <CircleX aria-hidden="true" strokeWidth={2} />
          {t('c.no')}
        </span>
      ),
    ),
    row(
      'time',
      x.time_stop_minutes ? <T k="c.within" nodes={{ d: <Dur ms={x.time_stop_minutes * MIN} lang={lang} /> }} /> : '—',
      held !== null ? <T k="c.heldFor" nodes={{ d: <Dur ms={held * MIN} lang={lang} /> }} /> : '—',
    ),
    row('invalid', x.invalidation ? <span dir="auto">{x.invalidation}</span> : '—', t(a.close_reason === 'invalidation' ? 'c.did' : 'c.didnt')),
    row(
      'costs',
      '—',
      a.fees_usd || a.funding_usd ? <T k="c.fees" nodes={{ fees: <Fig>{usdText(a.fees_usd, lang)}</Fig>, funding: <Fig>{usdText(a.funding_usd, lang)}</Fig> }} /> : '—',
    ),
  ]
  return (
    <table className="jr-compare-t" data-testid="journal-compare">
      <thead>
        <tr>
          <th scope="col">
            <span className="sr-only">{t('compare')}</span>
          </th>
          <th scope="col">{t('c.expected')}</th>
          <th scope="col">{t('c.actual')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ k, exp, act }) => (
          <tr key={k} data-row={k}>
            <th scope="row">{t(`c.${k}`)}</th>
            <td>{exp}</td>
            <td>{act}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function LessonBlock({ e, lang }: { e: JournalEntry; lang: Lang }) {
  const { t } = useTranslation('journal')
  if (lessonPending(e)) {
    return (
      <section className="jr-lesson is-pending" aria-label={t('lesson')} data-testid="journal-lesson">
        <p className="jr-lesson-k">
          <PenLine aria-hidden="true" strokeWidth={1.75} />
          <span>{t('pending')}</span>
        </p>
        <p className="jr-lesson-pend">{t('pendingLong')}</p>
      </section>
    )
  }
  const lesson = lessonText(e)
  if (!lesson) return null
  const closed = e.data.actual?.closed_at
  // created_at is when the row (and its lesson) was written, unless a later retry filled the lesson in.
  const timed = closed && (e.data.lesson_attempts ?? 1) <= 1
  const mins = timed ? isoMs(e.created_at) - isoMs(closed) : 0
  return (
    <section className="jr-lesson" aria-label={t('lesson')} data-testid="journal-lesson">
      <p className="jr-lesson-k">
        <Lightbulb aria-hidden="true" strokeWidth={1.75} />
        <span>{t('lesson')}</span>
      </p>
      <p className="jr-lesson-text">
        <Inline text={lesson} />
      </p>
      <p className="jr-lesson-by">
        <Medallion slug="portfolio_manager" size={22} />
        <span>
          {timed && mins >= 0 ? (
            <T k="lessonBy" nodes={{ time: <span className="num">{formatTime(e.created_at, lang)}</span>, mins: <Dur ms={mins} lang={lang} /> }} />
          ) : (
            t('lessonByPlain')
          )}
        </span>
      </p>
    </section>
  )
}

function Shape({ e, lang }: { e: JournalEntry; lang: Lang }) {
  const { t } = useTranslation('journal')
  const g = miniGeometry(e)
  const a = e.data.actual ?? {}
  const r = realisedR(e)
  if (!g || !a.mfe_r || !a.mae_r || r === null) return null
  const best = rText(a.mfe_r, 2)
  const worst = rText(a.mae_r, 2)
  const exit = rText(r, 2)
  const won = Number(r) > 0 && Number(a.mfe_r) > 0
  const kept = won ? `${formatFixed(String(Math.min(1, Number(r) / Number(a.mfe_r)) * 100), 0, lang)}%` : null
  return (
    <section className="jr-sec jr-shape" aria-labelledby="jr-h-shape">
      <h3 className="jr-h" id="jr-h-shape">
        {t('shape')}
      </h3>
      <div role="img" aria-label={t('shapeAria', { worst, best, exit })}>
        <MiniLadder g={g} size="wide" />
      </div>
      <p className="jr-shape-cap">
        {kept ? (
          <T k="shapeCap" nodes={{ best: <Fig>{best}</Fig>, worst: <Fig>{worst}</Fig>, exit: <Fig>{exit}</Fig>, kept: <Fig>{kept}</Fig> }} />
        ) : (
          <T k="shapeCapLoss" nodes={{ best: <Fig>{best}</Fig>, worst: <Fig>{worst}</Fig>, exit: <Fig>{exit}</Fig> }} />
        )}
      </p>
      <p className="jr-shape-key">{t('shapeKey')}</p>
    </section>
  )
}

export interface EntryViewProps {
  e: JournalEntry
  lang: Lang
  /** On a narrow window, the "‹ Journal" back link to the list. */
  backHref?: string
  /** A tag or agent chip filters the list by it. */
  tagHref: (tag: string) => string
  agentHref: (agent: string) => string
}

export const JournalEntryView = forwardRef<HTMLElement, EntryViewProps>(function JournalEntryView({ e, lang, backHref, tagHref, agentHref }, ref) {
  const { t } = useTranslation(['journal', 'warroom'])
  const tagLabel = useTagLabel()
  const outcome = outcomeOf(e)
  const OutIcon = OUTCOME_ICON[outcome]
  const side = sideOf(e)
  const rule = closeLabel(e)
  const a = e.data.actual ?? {}
  const inst = e.inst_id ?? '—'
  const opened = a.opened_at
  const closed = closedAt(e)
  const held = a.holding_minutes ?? (opened ? (isoMs(closed) - isoMs(opened)) / MIN : null)
  const agentName = (s: string) => t(`warroom:agents.${s}.name`, { defaultValue: s })
  const votes = e.data.votes ?? []
  const agents = e.data.agents ?? []
  const tags = displayTags(e.tags)
  const review = e.data.review
  const hasPlan = Boolean(e.data.expected || e.data.actual)
  const daRating = e.data.da_rating
  return (
    <article className="jr-doc is-enter" aria-label={t('entryAria', { inst })} tabIndex={-1} ref={ref} data-testid="journal-entry" data-id={e.id}>
      {backHref && (
        <Link to={backHref} className="jr-back">
          <ChevronLeft aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
          <span>{t('back')}</span>
        </Link>
      )}
      <header className="jr-e-head">
        <div className="jr-e-title">
          <h2 className="jr-e-inst">
            <Inst id={inst} />
          </h2>
          <p className="jr-e-sub">
            {side && <span className="jr-side is-chip">{t(side)}</span>}
            {opened && (
              <span>
                <T k="opened" nodes={{ when: <span className="num">{whenText(opened, lang)}</span> }} />
              </span>
            )}
            <span>
              <T k="closedAt" nodes={{ when: <span className="num">{whenText(closed, lang)}</span> }} />
            </span>
            {held !== null && (
              <span>
                <T k="held" nodes={{ d: <Dur ms={held * MIN} lang={lang} /> }} />
              </span>
            )}
          </p>
        </div>
        <div className="jr-e-result">
          <p className="jr-e-r">
            <SignedR value={realisedR(e)} />
          </p>
          <p className="jr-e-usd">
            <Fig>{usdText(a.realised_pnl_usd, lang)}</Fig>
          </p>
          <p className={cx('jr-e-out', `is-${outcome}`)} data-testid="journal-outcome">
            <OutIcon aria-hidden="true" strokeWidth={2.25} />
            <span>{t(`out.${outcome}`)}</span>
            {rule && <span className="jr-e-rule">{t(`rule.${rule}`)}</span>}
          </p>
        </div>
      </header>

      <LessonBlock e={e} lang={lang} />
      <Shape e={e} lang={lang} />

      <section className="jr-sec" aria-labelledby="jr-h-cmp">
        <h3 className="jr-h" id="jr-h-cmp">
          {t('compare')}
        </h3>
        {hasPlan ? (
          <CompareTable e={e} lang={lang} />
        ) : (
          <div className="jr-md-cmp" data-testid="journal-compare">
            <div>
              <h4>{t('c.expected')}</h4>
              {e.expected_md ? <Markdown text={e.expected_md} /> : <p>—</p>}
            </div>
            <div>
              <h4>{t('c.actual')}</h4>
              {e.actual_md ? <Markdown text={e.actual_md} /> : <p>—</p>}
            </div>
          </div>
        )}
      </section>

      {!lessonPending(e) && (review?.right_md || review?.wrong_md) && (
        <section className="jr-sec" aria-labelledby="jr-h-rw">
          <h3 className="jr-h" id="jr-h-rw">
            {t('review')}
          </h3>
          <div className="jr-rws">
            {review.right_md && (
              <div className="jr-rw is-right">
                <h4>
                  <ThumbsUp aria-hidden="true" strokeWidth={1.75} />
                  <span>{t('rightH')}</span>
                </h4>
                <Markdown text={review.right_md} />
              </div>
            )}
            {review.wrong_md && (
              <div className="jr-rw is-wrong">
                <h4>
                  <ThumbsDown aria-hidden="true" strokeWidth={1.75} />
                  <span>{t('wrongH')}</span>
                </h4>
                <Markdown text={review.wrong_md} />
              </div>
            )}
          </div>
        </section>
      )}

      {votes.length > 0 && (
        <section className="jr-sec" aria-labelledby="jr-h-team">
          <h3 className="jr-h" id="jr-h-team">
            {t('team')}
          </h3>
          <p className="jr-sec-sub">{t('teamSub')}</p>
          <ul className="jr-votes">
            {votes.map((v) => {
              const right = voteRight(v.vote, outcome)
              const conf = pct(v.confidence, lang)
              return (
                <li key={v.agent} className="jr-vote">
                  <Medallion slug={v.agent} size={28} />
                  <span className="jr-vote-who">{agentName(v.agent)}</span>
                  <span className="jr-vote-what">
                    <b>{t(`v.${v.vote}`, { defaultValue: v.vote })}</b>
                    {conf && (
                      <>
                        {lang === 'ar' ? '، ' : ', '}
                        <T k="v.conf" nodes={{ p: <Fig>{conf}</Fig> }} />
                      </>
                    )}
                    {v.agent === 'devils_advocate' && daRating !== null && daRating !== undefined && daRating !== '' && (
                      <>
                        {'. '}
                        <T k="v.rating" nodes={{ n: <Fig>{String(daRating)}</Fig> }} />
                      </>
                    )}
                  </span>
                  {right !== null && (
                    <span className={cx('jr-vote-res', right ? 'is-right' : 'is-wrong')}>
                      {right ? <CircleCheck aria-hidden="true" strokeWidth={2} /> : <CircleX aria-hidden="true" strokeWidth={2} />}
                      <span>{t(right ? 'v.right' : 'v.wrong')}</span>
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {agents.length > 0 && (
        <section className="jr-sec" aria-labelledby="jr-h-named">
          <h3 className="jr-h" id="jr-h-named">
            {t('named')}
          </h3>
          <div className="jr-named" data-testid="journal-agents">
            {agents.map((s) => (
              <Link key={s} to={agentHref(s)} className="jr-agent" title={t('namedTip', { name: agentName(s) })}>
                <Medallion slug={s} size={22} />
                <span>{agentName(s)}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <footer className="jr-e-foot">
        {tags.length > 0 && (
          <div className="jr-e-tags" role="group" aria-label={t('tags')}>
            {tags.map((g) => (
              <Link key={g} to={tagHref(g)} className="jr-tag is-button" title={t('tagTip', { tag: tagLabel(g) })}>
                {tagLabel(g)}
              </Link>
            ))}
          </div>
        )}
        {e.proposal_id && (
          <div className="jr-e-links">
            <Button asChild variant="secondary">
              <Link to={`/warroom?proposal=${encodeURIComponent(e.proposal_id)}`} data-testid="journal-open-wr">
                <MessagesSquare aria-hidden="true" strokeWidth={1.75} />
                <span>{t('openWr')}</span>
              </Link>
            </Button>
            <span className="jr-e-ref">
              <T k="prop" nodes={{ id: <Fig>{e.proposal_id.slice(0, 8)}</Fig> }} />
            </span>
          </div>
        )}
      </footer>
    </article>
  )
})
