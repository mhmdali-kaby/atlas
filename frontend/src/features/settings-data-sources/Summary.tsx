import { CircleAlert, CircleCheck, CircleMinus, Clock, KeyRound, TriangleAlert } from 'lucide-react'
import { useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../ui'
import { useDir } from '../../ui/useDir'
import {
  bucketStart,
  disabledUntil,
  downCauses,
  MIN,
  providerName,
  usSessions,
  wire,
  type ApiSource,
  type NoticeCategory,
  type Summary as SummaryModel,
  type SummaryState,
} from './model'
import { Fig, NS, Tr, useLang, useSourceName, useTimes } from './parts'

const TILE: Record<SummaryState, [typeof CircleCheck, string]> = {
  healthy: [CircleCheck, 'is-ok'],
  trouble: [TriangleAlert, 'is-warning'],
  noneWorking: [CircleAlert, 'is-error'],
  firstRun: [Clock, ''],
  stalled: [TriangleAlert, 'is-warning'],
  allOff: [CircleMinus, ''],
}

const listFormat = (items: string[], lang: string) =>
  new Intl.ListFormat(lang === 'ar' ? 'ar' : 'en', { type: 'conjunction' }).format(items)

function SummaryText({ sm, now, onKeys }: { sm: SummaryModel; now: number; onKeys: (providers: string[]) => void }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const time = useTimes(now)
  const nameOf = useSourceName()

  const title = sm.state === 'trouble' ? <Tr k="sum.trouble" count={sm.trouble.length} /> : <>{t(`sum.${sm.state}`)}</>
  let sub: ReactNode = null
  if (sm.state === 'healthy') sub = sm.newest !== null ? t('sub.newest', { ago: time.ago(sm.newest) }) : t('sub.noHeadlines')
  if (sm.state === 'trouble' || sm.state === 'noneWorking') {
    sub = (
      <>
        {sm.trouble.slice(0, 2).map((x, i) => {
          const until = disabledUntil(x.s)
          return (
            <span key={x.s.key}>
              {i > 0 && ' '}
              {x.st === 'retrying' && until !== null ? (
                <Tr k="sub.retrying" values={{ name: nameOf(x.s), time: time.when(until) }} />
              ) : (
                <Tr k="sub.failing" values={{ name: nameOf(x.s) }} />
              )}
            </span>
          )
        })}
        {sm.trouble.length > 2 && (
          <>
            {' '}
            <Tr k="sub.more" count={sm.trouble.length - 2} />
          </>
        )}
      </>
    )
  }
  if (sm.state === 'firstRun') sub = t('sub.firstRun')
  if (sm.state === 'stalled') {
    const mins = Math.max(1, Math.floor((now - (sm.newestCheck ?? now)) / MIN))
    sub = (
      <Tr k="sub.stalled" values={{ mins: t('mins', { count: mins }) }} />
    )
  }
  if (sm.state === 'allOff') sub = t('sub.allOff')

  const showKeys = sm.missingKeys.length > 0 && sm.state !== 'allOff' && sm.state !== 'stalled'
  const names = sm.missingKeys.map(providerName)
  return (
    <>
      <h2 className="src-sum-title" id="src-sum-title" data-testid="src-sum-title">
        {title}
      </h2>
      {sub && <p className="src-sum-sub">{sub}</p>}
      {showKeys && (
        <p className="src-sum-keys" data-testid="src-sum-keys">
          <KeyRound aria-hidden="true" strokeWidth={2} />
          <span>
            {names.length === 1 ? t('sub.keyOne', { name: names[0] }) : t('sub.keyMany', { names: listFormat(names, lang) })}
          </span>
          <Button size="sm" variant="plain" className="src-sum-keys-btn" data-testid="src-sum-add-key" onClick={() => onKeys(sm.missingKeys)}>
            {t(names.length === 1 ? 'keys.add' : 'keys.addMany')}
          </Button>
        </p>
      )}
    </>
  )
}

function OutageNotice({
  cat,
  sm,
  now,
  onShow,
}: {
  cat: NoticeCategory
  sm: SummaryModel
  now: number
  onShow: (cat: NoticeCategory) => void
}) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const time = useTimes(now)
  const c = downCauses(sm.rows, cat, now)
  const parts = (['retrying', 'needs_key', 'off'] as const).filter((k) => c[k] > 0).map((k) => t(`down.p.${k}`, { count: c[k] }))
  return (
    <div className="src-notice is-error" role="alert" data-testid="src-down" data-cat={cat}>
      <CircleAlert aria-hidden="true" strokeWidth={2} />
      <div>
        <p className="src-notice-title">{t(`down.${cat}`)}</p>
        <p className="src-notice-body">
          {parts.length > 0 && `${listFormat(parts, lang)}. `}
          {c.nextTry !== null && `${t('down.next', { time: time.when(c.nextTry) })} `}
          {t(`down.until.${cat}`)}
        </p>
        <div className="src-notice-actions">
          <Button variant="secondary" size="sm" onClick={() => onShow(cat)}>
            {t('down.show')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Wire({ rows, now }: { rows: ApiSource[]; now: number }) {
  const { t } = useTranslation(NS)
  const time = useTimes(now)
  const dir = useDir()
  const w = wire(rows)
  const sessions = usSessions(now)
  const [active, setActive] = useState<number | null>(null)
  const [focused, setFocused] = useState(false)

  const range = (i: number) => `${time.clock(bucketStart(now, i))}–${i === 23 ? t('chart.now') : time.clock(bucketStart(now, i + 1))}`
  const valueText = (i: number) => {
    const n = w.hours[i]!.total
    const h = t('headlines', { count: n })
    return i === 23 ? t('chart.soFar', { headlines: h }) : h
  }
  const tipText = (i: number) => {
    const cats = w.cats.filter((c) => (w.hours[i]!.byCat[c.cat] ?? 0) > 0).map((c) => `${t(`cat.${c.cat}`)} ${w.hours[i]!.byCat[c.cat]}`)
    return [valueText(i), range(i), ...cats].join(', ')
  }

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const cur = active ?? 23
    const step = dir === 'rtl' ? -1 : 1
    let next: number | null = cur
    if (e.key === 'ArrowRight') next = cur + step
    else if (e.key === 'ArrowLeft') next = cur - step
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = 23
    else if (e.key === 'Escape') next = null
    else return
    e.preventDefault()
    setActive(next === null ? null : Math.max(0, Math.min(23, next)))
  }

  const empty = w.total === 0
  // The tooltip sits beside the bar: on the inline-end side for the first two thirds, else before it.
  const after = active !== null && active < 16
  const tipPos = active === null ? undefined : after ? { insetInlineStart: `calc(${((active + 1) / 24) * 100}% + 6px)` } : { insetInlineEnd: `calc(${((24 - active) / 24) * 100}% + 6px)` }

  return (
    <div className={empty ? 'src-wire is-entering is-empty' : 'src-wire is-entering'} data-testid="src-wire">
      <div className="src-wire-head">
        <h3 className="src-wire-title">{t('chart.title')}</h3>
        <span className="src-wire-total" data-testid="src-wire-total">
          <Fig>{w.total.toLocaleString('en-US')}</Fig>
        </span>
      </div>
      <div
        className="src-chart"
        role="group"
        aria-label={t(empty ? 'chart.empty' : 'chart.label')}
        tabIndex={empty ? undefined : 0}
        onKeyDown={empty ? undefined : onKey}
        onFocus={() => {
          setFocused(true)
          setActive((a) => a ?? 23)
        }}
        onBlur={() => {
          setFocused(false)
          setActive(null)
        }}
        onMouseLeave={() => !focused && setActive(null)}
      >
        <div className={sessions.length ? 'src-plot has-session' : 'src-plot'}>
          <span className="src-gridline is-max" />
          {!empty && (
            <span className="src-tick" aria-hidden="true">
              <Fig>{w.max}</Fig>
            </span>
          )}
          <span className="src-gridline is-base" />
          <div className="src-bars">
            {sessions.map((u) => (
              <span
                key={u.start}
                className="src-session"
                style={{ insetInlineStart: `${(u.start / 24) * 100}%`, width: `${((u.end - u.start) / 24) * 100}%` }}
              >
                <span>{t('chart.us')}</span>
              </span>
            ))}
            {w.hours.map((h, i) => (
              <span
                key={i}
                className={['src-col', h.total ? 'has-v' : '', i === 23 ? 'is-now' : '', active === i ? 'is-active' : ''].join(' ')}
                style={{ ['--i' as string]: i }}
                onMouseEnter={() => !empty && setActive(i)}
              >
                <i style={{ ['--h' as string]: (h.total / w.max).toFixed(4) }} />
              </span>
            ))}
            {active !== null && !empty && (
              <div className="src-tip" style={tipPos} aria-hidden="true">
                <p className="src-tip-v">{valueText(active)}</p>
                <p className="src-tip-t">
                  <Fig>{range(active)}</Fig>
                </p>
                {w.cats.some((c) => (w.hours[active]!.byCat[c.cat] ?? 0) > 0) && (
                  <div className="src-tip-rows">
                    {w.cats
                      .filter((c) => (w.hours[active]!.byCat[c.cat] ?? 0) > 0)
                      .map((c) => (
                        <span className="src-tip-row" key={c.cat}>
                          <span>{t(`cat.${c.cat}`)}</span>
                          <Fig>{w.hours[active]!.byCat[c.cat]}</Fig>
                        </span>
                      ))}
                  </div>
                )}
              </div>
            )}
          </div>
          {empty && <p className="src-chart-empty">{t('chart.empty')}</p>}
        </div>
        <div className="src-axis" aria-hidden="true">
          {[0, 6, 12, 18].map((i) => (
            <span key={i} className={i === 0 ? 'is-first' : undefined} style={{ gridColumn: i + 1 }}>
              <Fig>{time.clock(bucketStart(now, i))}</Fig>
            </span>
          ))}
          <span className="is-now" style={{ gridColumn: 24 }}>
            {t('chart.now')}
          </span>
        </div>
        <span className="sr-only" aria-live="polite">
          {focused && active !== null ? tipText(active) : ''}
        </span>
      </div>
      <table className="sr-only">
        <caption>{t('chart.title')}</caption>
        <thead>
          <tr>
            <th>{t('chart.hour')}</th>
            <th>{t('chart.title')}</th>
          </tr>
        </thead>
        <tbody>
          {w.hours.map((h, i) => (
            <tr key={i}>
              <td>{range(i)}</td>
              <td>{h.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!empty && (
        <p className="src-cats" data-testid="src-cats">
          {w.cats.map((c) => (
            <span className="src-cat-total" key={c.cat}>
              <span>{t(`cat.${c.cat}`)}</span>
              <Fig className="num">{c.n.toLocaleString('en-US')}</Fig>
            </span>
          ))}
        </p>
      )}
    </div>
  )
}

export function Summary({
  sm,
  rows,
  now,
  updatedAt,
  onShow,
  onKeys,
}: {
  sm: SummaryModel
  rows: ApiSource[]
  now: number
  updatedAt: number
  onShow: (cat: NoticeCategory) => void
  /** The missing-keys line's Add key (one provider) or Add keys (several). */
  onKeys: (providers: string[]) => void
}) {
  const time = useTimes(now)
  const [Icon, tone] = TILE[sm.state]
  return (
    <section className="src-group src-summary" aria-labelledby="src-sum-title" data-testid="src-summary" data-state={sm.state}>
      <div className="src-sum-head">
        <span className={tone ? `src-sum-icon ${tone}` : 'src-sum-icon'} aria-hidden="true">
          <Icon strokeWidth={2} />
        </span>
        <div className="src-sum-text" aria-live="polite">
          <SummaryText sm={sm} now={now} onKeys={onKeys} />
        </div>
        <span className="src-updated">
          <Tr k="updated" values={{ time: time.clock(updatedAt, true) }} />
        </span>
      </div>
      {sm.down.length > 0 && (
        <div className="src-notices">
          {sm.down.map((cat) => (
            <OutageNotice key={cat} cat={cat} sm={sm} now={now} onShow={onShow} />
          ))}
        </div>
      )}
      <Wire rows={rows} now={now} />
    </section>
  )
}
