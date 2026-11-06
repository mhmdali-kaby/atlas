import { ArrowRightToLine, ChevronRight, CircleAlert, CloudOff, Plug, TriangleAlert, Wallet } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed, type Lang } from '../../lib/format'
import { Button, Seal, Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import type { SettingsDoc } from '../onboarding'
import { formatAgo, formatClock } from '../portfolio/time'
import { cutRows, fieldError, toCents, type CutRow, type MoneyTexts, type RiskPreview } from './model'
import { Fig, fill, pctText, usd } from './parts'

/*
 * The "Next trade" hero (design §5.3): the equity line, the brief's sentence, the receipt
 * (Risk, Use, and Trades when used up), and the cut-line chart. Every figure comes from
 * GET /api/settings/risk-preview (ATL-129) except the chart's non-binding bars, which are
 * the same terms worked out from the draft and the settings in integer cents.
 */

const STALE_MS = 15 * 60_000

/** The day's cap the "Left today" bar is cut from: the draft when it is valid, else the saved one. */
function dailyCents(texts: MoneyTexts, doc: SettingsDoc): bigint {
  const f = 'max_new_exposure_per_day_usdt'
  return (fieldError(f, texts[f]) ? null : toCents(texts[f])) ?? toCents(String(doc.settings[f] ?? '0')) ?? 0n
}

export function BindsBadge({ small, fallback }: { small?: boolean; fallback?: boolean }) {
  const { t } = useTranslation('settings-money')
  return (
    <span className={cx('ml-binds', small && 'is-sm', fallback && 'is-fallback')} data-testid="binds-badge">
      <ArrowRightToLine aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
      <span>{t('c.binds')}</span>
    </span>
  )
}

interface Props {
  doc: SettingsDoc
  texts: MoneyTexts
  preview: RiskPreview | undefined
  failed: boolean
  pending: boolean
  invalid: boolean
  dirty: boolean
  /** The saved settings' preview, for the "was" captions; undefined while not dirty. */
  saved: RiskPreview | undefined
  seed: string
  now: number
  lang: Lang
  /** The pane's own env (the "Settings for: Demo | Live" row this preview belongs to). */
  env: SettingsDoc['env']
  onRetry: () => void
}

function EquityLine({ p, seed, now, lang }: { p: RiskPreview; seed: string; now: number; lang: Lang }) {
  const { t } = useTranslation('settings-money')
  const asOf = p.equity_as_of ? Date.parse(p.equity_as_of) : now
  const stale = now - asOf > STALE_MS
  const time = <bdi dir="ltr">{formatClock(asOf, lang, false)}</bdi>
  const equity = <Fig>{usd(toCents(p.equity_usd) ?? 0n, lang)}</Fig>
  const sizing = <Fig>{usd(toCents(p.sizing_equity_usd) ?? 0n, lang)}</Fig>
  return (
    <p className={cx('ml-equity', stale && 'is-stale')} data-testid="ml-equity">
      {stale ? (
        <TriangleAlert aria-hidden="true" className="ml-eq-warn" />
      ) : (
        <Seal seed={seed} size={20} detail="mini" className="ml-eq-seal" />
      )}
      <span>
        {stale ? (
          <>
            <span className="ml-eq-stale">{fill(t('eq.stale'), { relative: formatAgo(now - asOf, lang), time })}</span>
            {p.cap_binds && <> {fill(t('eq.capTail'), { sizing })}</>}
          </>
        ) : (
          fill(t(p.cap_binds ? 'eq.cap' : 'eq.plain'), { time, equity, sizing })
        )}
      </span>
    </p>
  )
}

function CutChart({ rows, use, p, doc, daily, lang }: { rows: CutRow[]; use: bigint; p: RiskPreview; doc: SettingsDoc; daily: bigint; lang: Lang }) {
  const { t } = useTranslation('settings-money')
  const { t: tOb } = useTranslation('onboarding')
  const max = rows.reduce((m, r) => (r.cents > m ? r.cents : m), 1n)
  const ratio = (c: bigint) => Number((c * 100000n) / max) / 100000
  const cut = ratio(use)
  const badgeInBar = cut <= 0.45
  const used = toCents(p.used_exposure_today_usd) ?? 0n
  const profile = tOb(`rp.${doc.settings.risk_profile}`)
  const source = (r: CutRow) => {
    if (r.key === 'max_usdt_per_trade') return t('c.tradeSrc')
    if (r.key === 'max_position_pct') return fill(t('c.positionSrc'), { pct: <Fig>{pctText(doc.settings.max_position_pct, lang)}</Fig>, profile })
    return fill(t('c.leftSrc'), { used: <Fig>{usd(used, lang)}</Fig>, daily: <Fig>{usd(daily, lang)}</Fig> })
  }
  return (
    <>
      <ul className="ml-cut" aria-label={t('c.label')} style={{ '--n': rows.length, '--cut': cut.toFixed(5) } as CSSProperties} data-testid="ml-cut">
        {rows.map((r, i) => (
          <li key={r.key} className={cx('ml-cut-row', r.binding && 'is-binding')} style={{ gridRow: i + 1 }} data-key={r.key} data-binding={r.binding || undefined}>
            <span className="ml-cut-name">
              <span className="ml-cut-label">{t(`c.${r.key}`)}</span>
              <span className="ml-cut-src">{source(r)}</span>
            </span>
            <span className={cx('ml-bar', use === 0n && 'is-zero', r.cents === 0n && 'is-empty')} style={{ '--v': ratio(r.cents).toFixed(5) } as CSSProperties}>
              <i className="ml-bar-a" aria-hidden="true" />
              <i className="ml-bar-b" aria-hidden="true" />
              {r.binding && badgeInBar && <BindsBadge small />}
            </span>
            <span className="ml-cut-val">
              <Fig>{usd(r.cents, lang)}</Fig>
              {r.binding && <BindsBadge small fallback={badgeInBar} />}
            </span>
          </li>
        ))}
        <li className="ml-cutline" aria-hidden="true" style={{ gridRow: '1 / -1' }}>
          <span className="ml-cut-track">
            <i />
          </span>
        </li>
      </ul>
      <p className="sr-only">{use === 0n ? t('c.captionZero') : fill(t('c.caption'), { use: <Fig>{usd(use, lang)}</Fig> })}</p>
    </>
  )
}

function Was({ now, before, lang }: { now: bigint; before: bigint | null; lang: Lang }) {
  const { t } = useTranslation('settings-money')
  if (before === null || before === now) return null
  return <span className="ml-was">{fill(t('r.was'), { v: <Fig>{usd(before, lang)}</Fig> })}</span>
}

function Figures({ p, texts, doc, saved, lang }: { p: RiskPreview; texts: MoneyTexts; doc: SettingsDoc; saved: RiskPreview | undefined; lang: Lang }) {
  const { t } = useTranslation('settings-money')
  const { t: tOb } = useTranslation('onboarding')
  const risk = toCents(p.max_risk_usd) ?? 0n
  const use = toCents(p.max_notional_usd) ?? 0n
  const sizing = toCents(p.sizing_equity_usd) ?? 0n
  const equity = toCents(p.equity_usd) ?? 0n
  const maxTrades = Number(texts.max_trades_per_day.replace(/[,\s]/g, ''))
  const tradesUsed = Number.isFinite(maxTrades) && maxTrades > 0 && p.trades_today >= maxTrades
  const exposureUsed = use === 0n && p.notional_binding_limit === 'max_new_exposure_per_day'
  const rows = cutRows(p, texts, doc)
  const bindingName = p.notional_binding_limit ? t(`c.${p.notional_binding_limit}`) : ''
  const profile = tOb(`rp.${doc.settings.risk_profile}`)

  const sentence = tradesUsed
    ? fill(t('s.trades'), { n: <Fig>{formatFixed(maxTrades, 0, lang)}</Fig> })
    : exposureUsed
      ? t('s.exposure')
      : fill(t(p.cap_binds ? 's.cap' : 's.plain'), {
          sizing: <Fig testId="ml-sizing">{usd(sizing, lang)}</Fig>,
          equity: <Fig testId="ml-sizing">{usd(equity, lang)}</Fig>,
          risk: <Fig testId="ml-risk">{usd(risk, lang)}</Fig>,
          use: <Fig testId="ml-use">{usd(use, lang)}</Fig>,
        })

  return (
    <div className="ml-body">
      <p className="ml-sentence" data-testid="ml-sentence">
        {sentence}
      </p>
      <div className="ml-receipt">
        {tradesUsed && (
          <div className="ml-line is-warn" data-testid="ml-line-trades">
            <span className="ml-line-k">{t('r.trades')}</span>
            <span className="ml-line-v">
              <span className="ml-v-main">
                <TriangleAlert aria-hidden="true" className="ml-warn-ic" />
                <span>{fill(t('r.tradesV'), { n: <Fig>{formatFixed(p.trades_today, 0, lang)}</Fig>, max: <Fig>{formatFixed(maxTrades, 0, lang)}</Fig> })}</span>
              </span>
            </span>
            <span className="ml-line-x">{t('r.tradesX')}</span>
          </div>
        )}
        <div className="ml-line" data-testid="ml-line-risk" data-binding={p.risk_binding_limit ?? undefined}>
          <span className="ml-line-k">{t('r.risk')}</span>
          <span className="ml-line-v">
            <Fig>{usd(risk, lang)}</Fig>
            <Was now={risk} before={saved ? toCents(saved.max_risk_usd) : null} lang={lang} />
          </span>
          <span className="ml-line-x">
            {fill(t('r.riskX'), { pct: <Fig>{pctText(doc.settings.max_loss_per_trade_pct, lang)}</Fig>, sizing: <Fig>{usd(sizing, lang)}</Fig>, profile })}
          </span>
          <Link className="ml-line-link" to="/settings/risk">
            {t('r.link')}
            <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
          </Link>
        </div>
        <div className={cx('ml-line', use === 0n && 'is-warn')} data-testid="ml-line-use" data-binding={p.notional_binding_limit ?? undefined}>
          <span className="ml-line-k">{t('r.use')}</span>
          <span className="ml-line-v">
            <span className="ml-v-main">
              {use === 0n && <TriangleAlert aria-hidden="true" className="ml-warn-ic" />}
              <Fig>{usd(use, lang)}</Fig>
            </span>
            <Was now={use} before={saved ? toCents(saved.max_notional_usd) : null} lang={lang} />
          </span>
          <span className="ml-line-x">{fill(t('r.useX'), { limit: bindingName })}</span>
          {rows && (
            <div className="ml-chart">
              <CutChart rows={rows} use={use} p={p} doc={doc} daily={dailyCents(texts, doc)} lang={lang} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function Preview({ doc, texts, preview, failed, pending, invalid, dirty, saved, seed, now, lang, env, onRetry }: Props) {
  const { t } = useTranslation('settings-money')
  const noEquity = preview?.reason === 'no_equity'
  const noFigs = failed || noEquity
  let inner
  if (failed) {
    inner = (
      <div className="ml-empty" role="alert" data-testid="ml-preview-error">
        <span className="ml-empty-tile is-error" aria-hidden="true">
          <CloudOff />
        </span>
        <div className="ml-empty-text">
          <p className="ml-empty-title">{t('pErr.title')}</p>
          <p className="ml-empty-body">{t('pErr.body')}</p>
        </div>
        <Button variant="secondary" size="sm" onClick={onRetry}>
          {t('pErr.retry')}
        </Button>
      </div>
    )
  } else if (!preview) {
    inner = <div className="ml-body ml-sk" aria-hidden="true" />
  } else if (noEquity) {
    inner = (
      <div className="ml-empty" data-testid="ml-no-equity">
        <span className="ml-empty-tile" aria-hidden="true">
          <Wallet />
        </span>
        <div className="ml-empty-text">
          <p className="ml-empty-title">{t('noEq.title')}</p>
          <p className="ml-empty-body">{t(`noEq.body.${env}`)}</p>
        </div>
        <Link to="/settings/connections" className="ml-empty-btn">
          <Plug aria-hidden="true" />
          <span>{t('noEq.btn')}</span>
        </Link>
      </div>
    )
  } else {
    inner = (
      <>
        <EquityLine p={preview} seed={seed} now={now} lang={lang} />
        <Figures p={preview} texts={texts} doc={doc} saved={dirty ? saved : undefined} lang={lang} />
        {invalid && (
          <p className="ml-invalid" data-testid="ml-invalid">
            <CircleAlert aria-hidden="true" />
            <span>{t('invalid')}</span>
          </p>
        )}
      </>
    )
  }
  return (
    <section className={cx('ml-prev-sec', pending && 'is-pending', invalid && 'is-invalid')} aria-labelledby="ml-prev-title">
      <h2 id="ml-prev-title" className="ml-group-title ml-prev-title">
        <span>{t(dirty && !noFigs ? 'prev.titleDraft' : 'prev.title')}</span>
        <span className="ml-spin" aria-hidden={pending ? undefined : true} aria-label={pending ? t('prev.updating') : undefined} role={pending ? 'status' : undefined}>
          <Spinner />
        </span>
      </h2>
      <div className="st-group ml-preview" aria-busy={pending} data-testid="ml-preview">
        {inner}
      </div>
      {!noFigs && <p className="ml-group-note">{t('foot')}</p>}
    </section>
  )
}
