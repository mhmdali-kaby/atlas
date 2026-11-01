import { Check, Flame, Info, TriangleAlert, Wrench, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../../lib/format'
import { fillSlots, slot } from '../slots'
import { Inline } from '../text'
import { resolvedItems, type DaRowStatus, type DevilsReview } from './debate'
import { Fig, Head, VoteChip } from './debateParts'
import './debate.css'

/*
 * The Devil's Advocate's review (brief §7.7, design v11 Phase 2 §6): the strongest objection first as
 * a pull quote, the 1–5 rating as five pips with words, the fixed 10-row checklist (OK, or the
 * problem with its number), the fix and the vote with its confidence. The re-rate (round 2) is the
 * same component in compact form: the new rating, "Was N of 5" and the items now OK.
 */

const ROW_ICON: Record<DaRowStatus, LucideIcon> = { ok: Check, serious: TriangleAlert, minor: Info }
const sevOf = (r: number) => (r <= 2 ? 'is-low' : r === 3 ? 'is-mid' : 'is-high')

function Rating({ n }: { n: number }) {
  const { t } = useTranslation('debate')
  const what = t(`r.${n}`)
  return (
    <span className="db-rating" role="img" aria-label={t('ratingAria', { n, what })} data-testid="db-rating" data-rating={n}>
      <span className={`db-pips ${sevOf(n)}`} aria-hidden="true">
        {[1, 2, 3, 4, 5].map((i) => (
          <i key={i} className={`db-pip${i <= n ? ' is-on' : ''}`} />
        ))}
      </span>
      <span className="db-rating-text" aria-hidden="true">
        {fillSlots(t('rating', { n: slot('n'), what: slot('what') }), { n: <Fig>{String(n)}</Fig>, what })}
      </span>
    </span>
  )
}

export function DevilsReviewCard({
  review,
  previous,
  target,
  onJump,
  ts,
  lang,
}: {
  review: DevilsReview
  /** The round-1 review a re-rate answers, when the feed has it. */
  previous: DevilsReview | null
  target: string | null
  onJump: (id: string) => void
  ts: string
  lang: Lang
}) {
  const { t } = useTranslation('debate')
  const sev = sevOf(review.rating)

  if (review.round === 2) {
    const resolved = previous ? resolvedItems(previous, review) : []
    return (
      <div className={`hv-card db-card db-review is-compact ${sev}`} role="group" aria-label={t('rerateKind')} data-testid="db-review" data-round="2">
        <Head icon={Flame} kind={t('rerateKind')} version={review.version} target={target} onJump={onJump} ts={ts} lang={lang} />
        <div className="db-rate-row">
          <Rating n={review.rating} />
          <VoteChip vote={review.vote} confidence={review.confidence} lang={lang} />
        </div>
        {(previous || resolved.length > 0) && (
          <p className="db-compact-line">
            {previous && (
              <span className="db-was" data-testid="db-was">
                {fillSlots(t('ratingWas', { n: slot('n') }), { n: <Fig>{String(previous.rating)}</Fig> })}
              </span>
            )}
            {resolved.map((item) => (
              <span key={item} className="db-resolved" data-testid="db-resolved">
                <Check className="icon" aria-hidden="true" />
                <span>{t('nowOk', { item: t(`ci.${item}`) })}</span>
              </span>
            ))}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className={`hv-card db-card db-review ${sev}`} role="group" aria-label={t('daKind')} data-testid="db-review" data-round="1">
      <Head icon={Flame} kind={t('daKind')} version={review.version} target={target} onJump={onJump} ts={ts} lang={lang} />
      {review.strongestObjection && (
        <blockquote className="db-quote" data-testid="db-objection">
          <p className="db-quote-k">
            <TriangleAlert className="icon" aria-hidden="true" />
            <span>{t('strongest')}</span>
          </p>
          <p className="db-quote-t" dir="auto">
            <Inline text={review.strongestObjection} flat />
          </p>
        </blockquote>
      )}
      <div className="db-rate-row">
        <Rating n={review.rating} />
        <VoteChip vote={review.vote} confidence={review.confidence} lang={lang} />
      </div>
      <div className="db-checklist">
        <h4 className="db-sub">{t('checklist')}</h4>
        <ul className="db-ci-list" data-testid="db-checklist">
          {review.rows.map((row) => {
            const Icon = ROW_ICON[row.status]
            return (
              <li key={row.item} className={`db-ci is-${row.status}`} data-testid="db-ci" data-item={row.item} data-status={row.status}>
                <span className="db-ci-ic">
                  <Icon className="icon" aria-hidden="true" />
                </span>
                <span className="db-ci-name">{t(`ci.${row.item}`)}</span>
                <span className="db-ci-text" dir="auto">
                  <b className="db-ci-st">{t(`st.${row.status}`)}</b>
                  {row.detail && (
                    <>
                      {' '}
                      <Inline text={row.detail} flat />
                    </>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      </div>
      {review.fix && (
        <p className="db-fix" data-testid="db-fix">
          <Wrench className="icon" aria-hidden="true" />
          <span>
            <b>{t('fix')}</b>{' '}
            <span dir="auto">
              <Inline text={review.fix} flat />
            </span>
          </span>
        </p>
      )}
    </div>
  )
}
