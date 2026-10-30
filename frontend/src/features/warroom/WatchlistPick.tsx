import { ArrowDownRight, ArrowRight, ArrowUpRight, CircleMinus, Pin, Undo2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import { Button, Spinner } from '../../ui'
import { Fig } from './historianParts'
import { fillSlots, slot } from './slots'
import { Inline, InstrumentId } from './text'
import { baghdadDay, formatTime } from './time'
import { isDerivative, type Bias, type Pick, type WatchlistCard as Card } from './watchlist'
import { conv, css, price, Time } from './watchlistFormat'

/* One row of the watchlist card: a pick (or a removed / pin-only row) with its Remove/Undo action. */

const BIAS_ICON: Record<Bias, typeof ArrowUpRight> = { long: ArrowUpRight, short: ArrowDownRight, neutral: ArrowRight }

function BiasLabel({ bias }: { bias: Bias }) {
  const { t } = useTranslation('warroom')
  const Icon = BIAS_ICON[bias]
  return (
    <span className={`wl-bias is-${bias}`} data-testid="wl-bias">
      <span className="sr-only">{t('wl.biasSr')}: </span>
      <Icon aria-hidden="true" strokeWidth={2} className="icon-dir" data-testid={`wl-bias-icon-${bias}`} />
      <span>{t(`wl.bias.${bias}`)}</span>
    </span>
  )
}

function Conviction({ pick: p, card, lang }: { pick: Pick; card: Card; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const capped = p.convictionBeforeCap !== null
  let tip = ''
  if (capped) {
    const vars = { cap: conv(p.conviction, lang), before: conv(p.convictionBeforeCap!, lang) }
    if (p.capReason === 'news_class_down') {
      const since = card.degradedSince[p.assetClass]
      const cls = t(`wl.dcls.${p.assetClass}`, { defaultValue: t('wl.dcls.other') })
      tip = since ? t('wl.convCapTip.news_class_down', { ...vars, cls, time: formatTime(since, lang) }) : t('wl.convCapTip.news_class_down_open', { ...vars, cls })
    } else {
      tip = t('wl.convCapTip.partial_evidence', vars)
    }
  }
  const label = t('wl.convSr', { v: conv(p.conviction, lang) }) + (capped ? `. ${tip}` : '')
  return (
    <span
      className={`wl-conv${capped ? ' is-capped' : ''}`}
      role="img"
      aria-label={label}
      title={capped ? tip : undefined}
      tabIndex={capped ? 0 : undefined}
      data-testid="wl-conv"
      data-capped={capped || undefined}
    >
      <Fig>{conv(p.conviction, lang)}</Fig>
      <span className="wl-meter" aria-hidden="true">
        <i style={css({ '--v': p.conviction })} />
        {capped && <b style={css({ '--v': p.conviction })} />}
      </span>
    </span>
  )
}

function Chips({ pick: p, date, now, lang }: { pick: Pick; date: string; now: number; lang: Lang }) {
  const { t } = useTranslation('warroom')
  if (p.catalysts.length === 0) return null
  const weekday = (ms: number) => new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, { timeZone: 'Asia/Baghdad', weekday: 'short' }).format(new Date(ms))
  return (
    <ul className="wl-chips" data-testid="wl-chips">
      {p.catalysts.map((k) => {
        const ms = Date.parse(k.time)
        const otherDay = baghdadDay(ms) !== date
        return (
          <li
            key={`${k.time}|${k.name}`}
            className={`wl-chip${ms < now ? ' is-past' : ''}${k.importance >= 5 ? ' is-imp5' : ''}`}
            title={t('wl.rail.tip', { time: formatTime(ms, lang), name: k.name, imp: k.importance, insts: p.instId })}
            data-testid="wl-chip"
          >
            <i aria-hidden="true" />
            {otherDay && <span>{weekday(ms)}</span>}
            <b>
              <Time ts={ms} lang={lang} />
            </b>
            <span dir="auto">{k.name}</span>
          </li>
        )
      })}
    </ul>
  )
}

function Levels({ pick: p, lang }: { pick: Pick; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const segs: Array<[string, number]> = []
  if (p.levels.support !== null) segs.push(['support', p.levels.support])
  if (p.levels.resistance !== null) segs.push(['resistance', p.levels.resistance])
  if (!p.invalidation && p.levels.invalidation !== null) segs.push(['invalidation', p.levels.invalidation])
  return (
    <>
      {segs.length > 0 && (
        <p className="wl-levels" data-testid="wl-levels">
          {segs.map(([k, v]) => (
            <span key={k} className="wl-seg">
              <span>{t(`wl.${k}`)}</span> <Fig>{price(v, lang)}</Fig>
            </span>
          ))}
        </p>
      )}
      {p.invalidation && (
        <p className="wl-inval" data-testid="wl-inval">
          <span className="wl-inval-k">{t('wl.invalidation')}</span>
          <span dir="auto">
            <Inline text={p.invalidation} />
          </span>
        </p>
      )}
    </>
  )
}

export interface RowCtx {
  card: Card
  open: boolean
  now: number
  lang: Lang
  busy: Record<string, boolean>
  linkedInsts: string[] | null
  isNew: (inst: string) => boolean
  onRemove: (p: Pick) => void
  onUndo: (p: Pick) => void
  onHoverRow: (inst: string | null) => void
}

export function PickRow({ pick: p, ctx }: { pick: Pick; ctx: RowCtx }) {
  const { t } = useTranslation('warroom')
  const { card, open, lang, busy } = ctx
  const clsWord = (() => {
    const w = t(`wl.cls.${p.assetClass}`, { defaultValue: t('wl.cls.other') })
    return isDerivative(p.instId) ? w : t('wl.spotOf', { cls: w })
  })()
  const actions = !open ? null : busy[p.instId] ? (
    <span className="wl-busy">
      <Spinner />
    </span>
  ) : p.removedAt ? (
    <Button
      variant="plain"
      size="sm"
      className="wl-undo"
      icon={<Undo2 aria-hidden="true" strokeWidth={1.5} className="icon-dir" />}
      aria-label={t('wl.undoAria', { inst: p.instId })}
      onClick={() => ctx.onUndo(p)}
      data-wl-action="undo"
      data-testid="wl-undo"
    >
      {t('wl.undo')}
    </Button>
  ) : (
    <Button
      variant="plain"
      size="sm"
      className="wl-rm"
      icon={<CircleMinus aria-hidden="true" strokeWidth={1.5} />}
      aria-label={t('wl.removeAria', { inst: p.instId })}
      onClick={() => ctx.onRemove(p)}
      data-wl-action="remove"
      data-testid="wl-remove"
    >
      {t('wl.remove')}
    </Button>
  )
  const rank = (
    <span className="wl-rank" aria-hidden="true">
      {p.placeholder ? <Pin strokeWidth={1.5} /> : <span className="num">{p.rank}</span>}
    </span>
  )

  if (p.removedAt) {
    return (
      <li className="wl-pick is-removed" data-wl-row={p.instId} data-testid="wl-removed">
        {rank}
        <div className="wl-body">
          <p className="wl-l1">
            <span className="wl-inst">
              <InstrumentId id={p.instId} />
            </span>
            <span className="wl-rec">{fillSlots(t('wl.removedBy', { time: slot('time') }), { time: <Time ts={p.removedAt} lang={lang} /> })}</span>
          </p>
        </div>
        <div className="wl-act">{actions}</div>
      </li>
    )
  }

  const linked = ctx.linkedInsts?.includes(p.instId) ?? false
  const isNew = ctx.isNew(p.instId)
  return (
    <li
      className={`wl-pick${linked ? ' is-linked' : ''}${p.placeholder ? ' is-pin-only' : ''}`}
      data-wl-row={p.instId}
      data-testid="wl-pick"
      onMouseEnter={() => ctx.onHoverRow(p.instId)}
      onMouseLeave={() => ctx.onHoverRow(null)}
    >
      {rank}
      <div className="wl-body">
        <div className="wl-l1">
          <p className="wl-name">
            <span className="wl-inst" data-testid="wl-inst">
              <InstrumentId id={p.instId} />
            </span>
            <span className="wl-class" data-testid="wl-class">
              {clsWord}
            </span>
            {isNew && <span className="wl-badge wl-new">{t('wl.new')}</span>}
          </p>
          {!p.placeholder && (
            <span className="wl-call">
              <BiasLabel bias={p.bias} />
              <Conviction pick={p} card={card} lang={lang} />
            </span>
          )}
        </div>
        {p.pinned && (
          <p className="wl-pinned" data-testid="wl-pinned">
            {!p.placeholder && <Pin aria-hidden="true" strokeWidth={1.5} />}
            <span>{t('wl.pinnedBy')}</span>
          </p>
        )}
        {p.placeholder ? (
          <p className="wl-pin-note">{t('wl.pinnedNote')}</p>
        ) : (
          <>
            {p.thesis && (
              <p className="wl-thesis" dir="auto" data-testid="wl-thesis">
                <Inline text={p.thesis} />
              </p>
            )}
            <Chips pick={p} date={card.date} now={ctx.now} lang={lang} />
            <Levels pick={p} lang={lang} />
          </>
        )}
      </div>
      <div className="wl-act">{actions}</div>
    </li>
  )
}
