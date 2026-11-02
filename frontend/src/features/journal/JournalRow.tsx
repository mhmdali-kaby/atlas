import { PenLine } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { Lang } from '../../lib/format'
import { cx } from '../../ui/cx'
import { Inline } from '../warroom/text'
import { rText } from '../warroom/cards/rText'
import type { JournalEntry } from './api'
import { closedAt, closeLabel, displayTags, lessonPending, lessonText, miniGeometry, outcomeOf, realisedR, sideOf } from './model'
import { usdText, useTagLabel, whenText } from './format'
import { Fig, Inst, MiniLadder, OutcomeBadge, SignedR } from './parts'

/** One post-mortem in the list (design §7 "Row (JournalRow)"). */
export function JournalRow({ e, href, selected, lang, onOpen }: { e: JournalEntry; href: string; selected: boolean; lang: Lang; onOpen: () => void }) {
  const { t } = useTranslation('journal')
  const tagLabel = useTagLabel()
  const outcome = outcomeOf(e)
  const side = sideOf(e)
  const r = realisedR(e)
  const usd = usdText(e.data.actual?.realised_pnl_usd, lang)
  const rule = closeLabel(e)
  const when = whenText(closedAt(e), lang)
  const tags = displayTags(e.tags)
  const geo = miniGeometry(e)
  const pending = lessonPending(e)
  const lesson = lessonText(e)
  const inst = e.inst_id ?? '—'
  const aria = t('rowAria', {
    inst,
    side: side ? t(side) : '',
    outcome: t(`out.${outcome}`),
    r: r === null ? '—' : rText(r, 2),
    usd,
    when,
  })
  return (
    <Link
      to={href}
      replace={false}
      className={cx('jr-row', `is-${outcome}`)}
      aria-current={selected ? 'true' : undefined}
      aria-label={aria}
      data-testid="journal-row"
      data-id={e.id}
      onClick={(ev) => {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return
        ev.preventDefault()
        onOpen()
      }}
    >
      <OutcomeBadge outcome={outcome} />
      <span className="jr-row-main">
        <span className="jr-row-top">
          <Inst id={inst} />
          {side && <span className="jr-side">{t(side)}</span>}
        </span>
        <span className="jr-row-meta">
          <span className="num" data-testid="row-when">
            {when}
          </span>
          {rule && <span className="jr-row-rule">{t(`rule.${rule}`)}</span>}
        </span>
        {pending ? (
          <span className="jr-row-lesson is-pending">
            <PenLine aria-hidden="true" strokeWidth={1.75} />
            <span>{t('pending')}</span>
          </span>
        ) : (
          lesson && (
            <span className="jr-row-lesson">
              <Inline text={lesson} flat />
            </span>
          )
        )}
        {tags.length > 0 && (
          <span className="jr-row-tags">
            {tags.slice(0, 2).map((g) => (
              <span key={g} className="jr-tag">
                {tagLabel(g)}
              </span>
            ))}
            {tags.length > 2 && (
              <span className="jr-tag is-more" aria-label={t('moreTags', { tags: tags.slice(2).map(tagLabel).join(', ') })} role="note">
                <Fig>{`+${tags.length - 2}`}</Fig>
              </span>
            )}
          </span>
        )}
      </span>
      <span className="jr-row-figs">
        <SignedR value={r} className="jr-row-r" />
        <span className="jr-row-usd" data-testid="row-usd">
          <Fig>{usd}</Fig>
        </span>
        {geo && <MiniLadder g={geo} />}
      </span>
    </Link>
  )
}
