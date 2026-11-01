import {
  ChartCandlestick,
  CircleDot,
  CircleMinus,
  MoveHorizontal,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { formatFixed, type Lang } from '../../../lib/format'
import { useNow } from '../../portfolio/hooks'
import { threadPath, useSelectedThread } from '../selection'
import { fillSlots, slot } from '../slots'
import { InstrumentId, Inline } from '../text'
import { baghdadDay, formatDate, formatFullTime, formatTime, formatUtcFull, formatUtcTime } from '../time'
import { Ladder } from './Ladder'
import { rText } from './rText'
import { isThin, type Bias, type Direction, type TechnicalSetup } from './technicalSetup'
import './technicalSetup.css'

/*
 * The Technical Analyst's TechnicalSetup card (brief §7.4, design v11 Phase 2 §7): the call as the
 * headline ("Long breakout", or "No setup" with the one condition that would create one), the bias
 * by timeframe, the plan on the trade ladder, then trigger, targets, expected R, quality, slippage,
 * invalidation and the levels used. Every direction and bias carries an icon and a word.
 */

const TF_ICON: Record<Bias, LucideIcon> = { up: TrendingUp, down: TrendingDown, range: MoveHorizontal }
const DIR_ICON: Record<Direction, LucideIcon> = { long: TrendingUp, short: TrendingDown, none: CircleMinus }

function Fig({ children }: { children: string }) {
  return (
    <bdi dir="ltr" className="fig num">
      {children}
    </bdi>
  )
}

/** "Data as of 14:00 (11:00 UTC)", both full dates in the tooltip. */
export function AsOf({ ts, lang }: { ts: string; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const now = useNow(60_000)
  const today = baghdadDay(ts) === baghdadDay(now)
  const when = today ? formatTime(ts, lang) : `${formatDate(ts, lang)}${lang === 'ar' ? '، ' : ', '}${formatTime(ts, lang)}`
  return (
    <time className="hv-asof" dateTime={ts} tabIndex={0} title={t('ta.dual', { baghdad: formatFullTime(ts, lang), utc: formatUtcFull(ts) })} data-testid="ta-asof">
      {fillSlots(t('ta.asOf', { time: slot('time'), utc: slot('utc') }), {
        time: <span className="num">{when}</span>,
        utc: <bdi dir="ltr" className="num">{`${formatUtcTime(ts)} UTC`}</bdi>,
      })}
    </time>
  )
}

/** The instrument: opens its own thread, where the Inspector shows it. Plain text when it has no thread. */
export function InstrumentButton({ instId }: { instId: string }) {
  const { t } = useTranslation('warroom')
  const navigate = useNavigate()
  const { list } = useSelectedThread()
  const thread = list.find((x) => x.kind === 'instrument' && x.key === instId)
  if (!thread) {
    return (
      <span className="an-inst" data-testid="ta-inst">
        <InstrumentId id={instId} />
      </span>
    )
  }
  const label = t('ta.inspect', { inst: instId })
  return (
    <button type="button" className="an-inst" aria-label={label} title={label} onClick={() => navigate(threadPath(thread))} data-testid="ta-inst">
      <InstrumentId id={instId} />
    </button>
  )
}

function BiasChips({ setup }: { setup: TechnicalSetup }) {
  const { t } = useTranslation('warroom')
  if (setup.bias.length === 0) return null
  return (
    <ul className="an-bias" aria-label={t('ta.bias')} data-testid="ta-bias">
      {setup.bias.map(({ tf, bias }) => {
        const Icon = TF_ICON[bias] ?? CircleDot
        const word = t(`ta.tf.${bias}`)
        return (
          <li key={tf} className={`an-tf is-${bias}`} aria-label={t('ta.tfAria', { tf, bias: word })} data-tf={tf}>
            <bdi dir="ltr" className="an-tf-k" aria-hidden="true">
              {tf}
            </bdi>
            <Icon className="icon" aria-hidden="true" />
            <span aria-hidden="true">{word}</span>
          </li>
        )
      })}
    </ul>
  )
}

function Head({ setup, lang }: { setup: TechnicalSetup; lang: Lang }) {
  const { t } = useTranslation('warroom')
  return (
    <div className="hv-head">
      <span className="hv-tile">
        <ChartCandlestick aria-hidden="true" strokeWidth={1.5} />
      </span>
      <span className="hv-kind">{t('ta.kind')}</span>
      <AsOf ts={setup.dataAsOf} lang={lang} />
    </div>
  )
}

function Title({ setup }: { setup: TechnicalSetup }) {
  const { t } = useTranslation('warroom')
  const Icon = DIR_ICON[setup.direction]
  const words = setup.setupType === 'none' || setup.direction === 'none' ? t('ta.noSetup') : t(`ta.title.${setup.direction}`, { setup: t(`ta.setup.${setup.setupType}`) })
  return (
    <p className={`an-title is-${setup.direction}`}>
      <InstrumentButton instId={setup.instId} />
      <span className="an-dir" data-testid="ta-headline">
        <Icon className="icon" aria-hidden="true" />
        <span>{words}</span>
      </span>
    </p>
  )
}

export function TechnicalSetupCard({ setup, lang }: { setup: TechnicalSetup; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const d = setup.tickDecimals
  const P = (p: string) => formatFixed(p, d, lang)

  if (setup.setupType === 'none' || !setup.entryZone || !setup.stop) {
    const condition = setup.noSetupReason ?? setup.trigger
    const showTrigger = setup.trigger.trim() !== '' && setup.trigger !== condition
    return (
      <div className="hv-card an-card an-ta is-none" role="group" aria-label={t('ta.kind')} data-testid="ta-card" data-setup="none">
        <Head setup={setup} lang={lang} />
        <Title setup={setup} />
        {condition && (
          <p className="an-cond" dir="auto" data-testid="ta-condition">
            <Inline text={condition} flat />
          </p>
        )}
        <BiasChips setup={setup} />
        {(showTrigger || setup.invalidation) && (
          <dl className="an-dl">
            {showTrigger && (
              <>
                <dt>{t('ta.trigger')}</dt>
                <dd dir="auto" data-testid="ta-trigger">
                  <Inline text={setup.trigger} flat />
                </dd>
              </>
            )}
            {setup.invalidation && (
              <>
                <dt>{t('ta.inval')}</dt>
                <dd dir="auto">
                  <Inline text={setup.invalidation} flat />
                </dd>
              </>
            )}
          </dl>
        )}
      </div>
    )
  }

  const thin = isThin(setup)
  const bps = setup.slippageBps === null ? null : <Fig>{`${formatFixed(setup.slippageBps, 0, lang)} bps`}</Fig>
  const side = setup.direction === 'short' ? 'short' : 'long'
  const ladderTargets = setup.targets.map((x) => ({ price: x.price, r: x.r, pct: x.closePct }))
  const q = setup.quality

  return (
    <div className="hv-card an-card an-ta" role="group" aria-label={t('ta.kind')} data-testid="ta-card" data-setup={setup.setupType}>
      <Head setup={setup} lang={lang} />
      <Title setup={setup} />
      <BiasChips setup={setup} />
      {thin && bps && (
        <p className="hv-warn an-thin" role="note" data-testid="ta-thin">
          <TriangleAlert aria-hidden="true" strokeWidth={2} />
          <span>{fillSlots(t('ta.thin', { bps: slot('bps') }), { bps })}</span>
        </p>
      )}
      <div className="an-ladder">
        <Ladder side={side} entry={setup.entryZone} stop={setup.stop} targets={ladderTargets} mark={setup.lastPrice} decimals={d} lang={lang} />
        <Ladder side={side} entry={setup.entryZone} stop={setup.stop} targets={ladderTargets} decimals={d} lang={lang} compact />
      </div>
      <dl className="an-dl">
        <dt>{t('ta.entry')}</dt>
        <dd data-testid="ta-entry">
          <Fig>{`${P(setup.entryZone[0])}–${P(setup.entryZone[1])}`}</Fig>
        </dd>
        <dt>{t('ta.stop')}</dt>
        <dd data-testid="ta-stop">
          <Fig>{P(setup.stop)}</Fig>
        </dd>
        {setup.trigger && (
          <>
            <dt>{t('ta.trigger')}</dt>
            <dd dir="auto" data-testid="ta-trigger">
              <Inline text={setup.trigger} flat />
            </dd>
          </>
        )}
        <dt>{t('ta.targets')}</dt>
        <dd className="is-row an-reasons">
          {setup.targets.map((x, i) => (
            <span key={i} className="an-reason" data-testid="ta-target">
              <b>{t('ld.tp', { n: i + 1 })}</b>
              <Fig>{P(x.price)}</Fig>
              {x.r && <Fig>{rText(x.r, 2)}</Fig>}
              {x.reason && <span dir="auto">{x.reason}</span>}
            </span>
          ))}
        </dd>
        {setup.expectedR && (
          <>
            <dt>{t('ta.expR')}</dt>
            <dd data-testid="ta-exp-r">
              <Fig>{`${formatFixed(setup.expectedR, 2, lang)}R`}</Fig>
            </dd>
          </>
        )}
        {q !== null && (
          <>
            <dt>{t('ta.quality')}</dt>
            <dd>
              <span className="an-q" role="img" aria-label={t('ta.qualityAria', { v: formatFixed(q, 2, lang) })} data-testid="ta-quality">
                <span className="an-meter" aria-hidden="true">
                  <i style={{ '--v': q.toFixed(3) } as CSSProperties} />
                </span>
                <Fig>{formatFixed(q, 2, lang)}</Fig>
              </span>
            </dd>
          </>
        )}
        {bps && (
          <>
            <dt>{t('ta.slip')}</dt>
            <dd className={`is-row${thin ? ' an-warn-ink' : ''}`} data-testid="ta-slip">
              {thin && <TriangleAlert className="an-ic" aria-hidden="true" />}
              <span>{fillSlots(t('ta.slipV', { bps: slot('bps') }), { bps })}</span>
            </dd>
          </>
        )}
        {setup.invalidation && (
          <>
            <dt>{t('ta.inval')}</dt>
            <dd dir="auto" data-testid="ta-inval">
              <Inline text={setup.invalidation} flat />
            </dd>
          </>
        )}
        {setup.levels.length > 0 && (
          <>
            <dt>{t('ta.levels')}</dt>
            <dd className="is-row an-levels">
              {setup.levels.map((l) => (
                <span key={l.id} className="an-level">
                  <span dir="auto">{l.label ?? l.id}</span>
                  <Fig>{l.zone ? `${P(l.zone[0])}–${P(l.zone[1])}` : P(l.price!)}</Fig>
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
    </div>
  )
}
