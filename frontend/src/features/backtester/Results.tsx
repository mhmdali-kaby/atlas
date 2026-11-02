import { useVirtualizer } from '@tanstack/react-virtual'
import { CircleMinus, FileWarning, SquareDashed, TriangleAlert } from 'lucide-react'
import { useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, formatSigned, priceDecimals, type Lang } from '../../lib/format'
import { SegmentedControl, SignedValue } from '../../ui'
import { cx } from '../../ui/cx'
import { formatDate, formatDateTime } from '../warroom/time'
import { useLang } from '../portfolio/hooks'
import { EquityCurve } from './EquityCurve'
import { Fig, NS } from './parts'
import { asResult, pairPos, pct, type BacktestResult, type Fold, type InstrumentResult, type Metrics, type Trade } from './result'
import { fill } from './text'

/*
 * A done run's results (design: backtester.js resultHtml). Notices first (overfit, gaps in the
 * candles), the instruments trading_core left out, an instrument switch when the run tested more
 * than one, then for the chosen instrument: the ten metric tiles over the whole period, the equity
 * curve, the walk-forward splits as fitted (ring) against tested (dot), and the trade list.
 */

/** Above this many rows the trade list is virtualised inside its own scroller. */
export const VIRTUAL_AFTER = 60
const TRADE_ROW_H = 57

const base = (id: string) => id.split('-')[0] ?? id

function NoneText({ k = 'r.m.none' }: { k?: string }) {
  const { t } = useTranslation(NS)
  return <span className="bt-none">{t(k)}</span>
}

/** A signed fraction-of-equity metric in percent, or "Not enough to say". */
function SignedPct({ v }: { v: number | null | undefined }) {
  const p = pct(v)
  return p === null ? <NoneText /> : <SignedValue value={p.toFixed(2)} unit="percent" />
}

function CostPct({ v, lang }: { v: number | null | undefined; lang: Lang }) {
  const p = pct(v)
  return p === null ? <NoneText /> : <Fig>{formatSigned(p.toFixed(2), 'percent', lang)}</Fig>
}

function Plain({ v, digits = 2, lang }: { v: number | null | undefined; digits?: number; lang: Lang }) {
  return v === null || v === undefined || !Number.isFinite(v) ? <NoneText /> : <Fig>{formatFixed(v, digits, lang)}</Fig>
}

function Tile({ id, label, sub, children }: { id: string; label: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="bt-tile" data-testid={`bt-m-${id}`}>
      <p className="bt-tile-label">{label}</p>
      <p className="bt-tile-value" data-testid={`bt-m-${id}-v`}>
        {children}
      </p>
      {sub && <p className="bt-tile-sub">{sub}</p>}
    </div>
  )
}

function Tiles({ inst, lang }: { inst: InstrumentResult; lang: Lang }) {
  const { t } = useTranslation(NS)
  const f: Metrics = inst.full
  const n = f.n_trades ?? 0
  const wins = f.win_rate === null ? null : Math.round(f.win_rate * n)
  const neg = (v: number | null | undefined) => (v === null || v === undefined ? null : -v)
  return (
    <section className="bt-panel bt-tiles" aria-label={t('r.metrics', { inst: inst.inst_id })} data-testid="bt-tiles" data-inst={inst.inst_id}>
      <Tile
        id="total"
        label={t('r.m.total')}
        sub={
          f.start && f.end
            ? fill(t('r.m.subTotal'), { from: formatDate(f.start, lang), to: formatDate(f.end, lang, true) })
            : undefined
        }
      >
        <SignedPct v={f.total_return} />
      </Tile>
      <Tile id="cagr" label={t('r.m.cagr')}>
        <SignedPct v={f.cagr} />
      </Tile>
      <Tile id="sharpe" label={t('r.m.sharpe')}>
        <Plain v={f.sharpe} lang={lang} />
      </Tile>
      <Tile id="sortino" label={t('r.m.sortino')}>
        <Plain v={f.sortino} lang={lang} />
      </Tile>
      <Tile id="dd" label={t('r.m.dd')}>
        <SignedPct v={f.max_drawdown} />
      </Tile>
      <Tile
        id="win"
        label={t('r.m.win')}
        sub={wins === null ? undefined : fill(t('r.m.subWin'), { w: <Fig>{formatFixed(wins, 0, lang)}</Fig>, n: <Fig>{formatFixed(n, 0, lang)}</Fig> })}
      >
        {f.win_rate === null ? <NoneText /> : <Fig>{`${formatFixed(f.win_rate * 100, 0, lang)}%`}</Fig>}
      </Tile>
      <Tile id="pf" label={t('r.m.pf')}>
        {f.profit_factor === null ? <NoneText k={n > 0 ? 'r.m.pfNone' : 'r.m.none'} /> : <Plain v={f.profit_factor} lang={lang} />}
      </Tile>
      <Tile id="trades" label={t('r.m.trades')}>
        <Fig>{formatFixed(n, 0, lang)}</Fig>
      </Tile>
      {/* Costs, as the design draws them: what they took from (or added to) equity, signed but not coloured. */}
      <Tile id="fees" label={t('r.m.fees')} sub={t('r.m.subFees')}>
        <CostPct v={neg(f.fees_paid)} lang={lang} />
      </Tile>
      <Tile id="funding" label={t('r.m.funding')} sub={t(inst.funding_included ? 'r.funding' : 'r.noFunding')}>
        {inst.funding_included ? <CostPct v={neg(f.funding_paid)} lang={lang} /> : <NoneText />}
      </Tile>
    </section>
  )
}

function span(m: Metrics, lang: Lang): string {
  return m.start && m.end ? `${formatDate(m.start, lang)}–${formatDate(m.end, lang)}` : '—'
}

function SharpePair({ fold, lang }: { fold: Fold; lang: Lang }) {
  const { t } = useTranslation(NS)
  const a = fold.in_sample.sharpe
  const b = fold.out_of_sample.sharpe
  const wide = a !== null && b !== null && a > 0 && b < a * 0.5
  const pa = pairPos(a)
  const pb = pairPos(b)
  const txt = (v: number | null) => (v === null ? '—' : formatFixed(v, 2, lang))
  return (
    <span className="bt-pair-cell">
      <span
        className={cx('bt-pair', wide && 'is-wide')}
        dir="ltr"
        role="img"
        aria-label={t('r.fold.pairAria', { is: txt(a), oos: txt(b) })}
        data-wide={wide ? 'true' : 'false'}
      >
        <i className="bt-pair-zero" />
        {a !== null && b !== null && <i className="bt-pair-gap" style={{ left: `${Math.min(pa, pb)}%`, width: `${Math.abs(pa - pb)}%` }} />}
        {a !== null && <i className="bt-pair-is" style={{ left: `${pa}%` }} />}
        {b !== null && <i className="bt-pair-oos" style={{ left: `${pb}%` }} />}
      </span>
      <span className="bt-pair-v" aria-hidden="true">
        <Fig>{txt(a)}</Fig> → <Fig>{txt(b)}</Fig>
      </span>
    </span>
  )
}

function FoldsTable({ inst, lang }: { inst: InstrumentResult; lang: Lang }) {
  const { t } = useTranslation(NS)
  return (
    <section className="bt-panel" aria-labelledby="bt-folds-h" data-testid="bt-folds">
      <div className="bt-sec-head">
        <h3 className="text-headline" id="bt-folds-h">
          {t('r.folds')}
        </h3>
        <p className="bt-sec-sub">{t('r.foldsSub')}</p>
      </div>
      {inst.folds.length === 0 ? (
        <p className="bt-sec-sub bt-empty">{t('r.noFolds')}</p>
      ) : (
        <div className="bt-scroll" tabIndex={0} role="group" aria-labelledby="bt-folds-h">
          <table className="bt-table">
            <thead>
              <tr>
                <th scope="col">{t('r.fold.n')}</th>
                <th scope="col">{t('r.fold.is')}</th>
                <th scope="col">{t('r.fold.oos')}</th>
                <th scope="col">{t('r.fold.sharpe')}</th>
                <th scope="col">{t('r.fold.ret')}</th>
                <th scope="col">{t('r.fold.trades')}</th>
              </tr>
            </thead>
            <tbody>
              {inst.folds.map((f) => (
                <tr key={f.fold} data-testid={`bt-fold-${f.fold}`}>
                  <th scope="row">
                    <Fig>{formatFixed(f.fold, 0, lang)}</Fig>
                  </th>
                  <td>
                    <Fig>{span(f.in_sample, lang)}</Fig>
                  </td>
                  <td>
                    <Fig>{span(f.out_of_sample, lang)}</Fig>
                  </td>
                  <td>
                    <SharpePair fold={f} lang={lang} />
                  </td>
                  <td>
                    <SignedPct v={f.out_of_sample.total_return} />
                  </td>
                  <td>
                    <Fig>{formatFixed(f.out_of_sample.n_trades ?? 0, 0, lang)}</Fig>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/** A fill price at display precision: the API's floats carry slippage to 12+ places. */
const priceText = (v: number, lang: Lang) => formatFixed(v, Math.abs(v) >= 100 ? priceDecimals(v) : Math.abs(v) >= 1 ? 4 : 6, lang)

function TradeRow({ r, i, lang, style }: { r: Trade; i: number; lang: Lang; style?: CSSProperties }) {
  const { t } = useTranslation(NS)
  return (
    <tr style={style} data-testid="bt-trade">
      <td>
        <Fig>{formatFixed(i + 1, 0, lang)}</Fig>
      </td>
      <td>{t(r.side === 'short' ? 'sideShort' : 'sideLong')}</td>
      <td>
        <span className="tabular-nums">{formatDateTime(r.entry_ts, lang)}</span>
        <br />
        <Fig className="text-label-secondary">{priceText(r.entry_price, lang)}</Fig>
      </td>
      <td>
        <span className="tabular-nums">{formatDateTime(r.exit_ts, lang)}</span>
        <br />
        <Fig className="text-label-secondary">{priceText(r.exit_price, lang)}</Fig>
      </td>
      <td>
        <SignedValue value={Number(r.return_pct).toFixed(2)} unit="percent" />
      </td>
      <td>
        <SignedValue value={(Number(r.pnl) * 100).toFixed(2)} unit="percent" />
      </td>
      <td>
        <Fig>{`${formatFixed(Number(r.fees) * 100, 2, lang)}%`}</Fig>
      </td>
    </tr>
  )
}

function TradesTable({ inst, lang }: { inst: InstrumentResult; lang: Lang }) {
  const { t } = useTranslation(NS)
  const scroller = useRef<HTMLDivElement>(null)
  const rows = inst.trades
  const virtual = rows.length > VIRTUAL_AFTER
  const virtualizer = useVirtualizer({
    count: virtual ? rows.length : 0,
    getScrollElement: () => scroller.current,
    estimateSize: () => TRADE_ROW_H,
    overscan: 10,
    initialRect: { width: 900, height: 520 },
  })
  const items = virtualizer.getVirtualItems()
  const padTop = items.length > 0 ? items[0]!.start : 0
  const padBottom = items.length > 0 ? virtualizer.getTotalSize() - items[items.length - 1]!.end : 0

  return (
    <section className="bt-panel" aria-labelledby="bt-trades-h" data-testid="bt-trades" data-virtual={virtual ? 'true' : 'false'}>
      <div className="bt-sec-head">
        <h3 className="text-headline" id="bt-trades-h">
          {t('r.trades')}
        </h3>
        <p className="bt-sec-sub">
          {t('r.tradesSub', { count: inst.trades_total, n: formatFixed(inst.trades_total, 0, lang) })}
          {inst.trades_truncated && ` ${t('r.tradesCut', { shown: formatFixed(rows.length, 0, lang), total: formatFixed(inst.trades_total, 0, lang) })}`}
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="bt-sec-sub bt-empty">{t('r.noTrades')}</p>
      ) : (
        <div ref={scroller} className={cx('bt-scroll', virtual && 'is-virtual')} tabIndex={0} role="group" aria-labelledby="bt-trades-h">
          <table className="bt-table is-trades" aria-rowcount={rows.length + 1}>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">{t('r.t.side')}</th>
                <th scope="col">{t('r.t.in')}</th>
                <th scope="col">{t('r.t.out')}</th>
                <th scope="col">{t('r.t.ret')}</th>
                <th scope="col">{t('r.t.pnl')}</th>
                <th scope="col">{t('r.t.fees')}</th>
              </tr>
            </thead>
            <tbody>
              {virtual ? (
                <>
                  {padTop > 0 && (
                    <tr aria-hidden="true" className="bt-pad-row">
                      <td colSpan={7} style={{ height: padTop }} />
                    </tr>
                  )}
                  {items.map((it) => (
                    <TradeRow key={it.key} r={rows[it.index]!} i={it.index} lang={lang} style={{ height: TRADE_ROW_H }} />
                  ))}
                  {padBottom > 0 && (
                    <tr aria-hidden="true" className="bt-pad-row">
                      <td colSpan={7} style={{ height: padBottom }} />
                    </tr>
                  )}
                </>
              ) : (
                rows.map((r, i) => <TradeRow key={i} r={r} i={i} lang={lang} />)
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Notices({ result, lang }: { result: BacktestResult; lang: Lang }) {
  const { t } = useTranslation(NS)
  const many = result.instruments.length > 1
  const overfit = result.instruments.filter((x) => x.overfit_warning)
  const sharpe = (v: number | null) => <Fig>{v === null ? '—' : formatFixed(v, 2, lang)}</Fig>
  const notes = result.instruments.filter((x) => x.data_gap || x.gap_notes.length > 0)
  return (
    <>
      {overfit.map((x) => (
        <div key={`o-${x.inst_id}`} className="bt-notice is-warning" role="alert" data-testid="bt-overfit">
          <TriangleAlert aria-hidden="true" strokeWidth={1.75} />
          <div>
            <p className="bt-notice-title">{t('r.overfit')}</p>
            <p className="bt-notice-body">
              {many && (
                <>
                  <Fig>{x.inst_id}</Fig>
                  {': '}
                </>
              )}
              {fill(t('r.overfitBody'), { is: sharpe(x.is_sharpe_mean), oos: sharpe(x.oos_sharpe_mean) })}
            </p>
          </div>
        </div>
      ))}
      {/* The run says it is overfit but no instrument carries the numbers: the title alone. */}
      {result.overfit_warning && overfit.length === 0 && (
        <div className="bt-notice is-warning" role="alert" data-testid="bt-overfit">
          <TriangleAlert aria-hidden="true" strokeWidth={1.75} />
          <div>
            <p className="bt-notice-title">{t('r.overfit')}</p>
          </div>
        </div>
      )}
      {notes.map((x) =>
        x.data_gap ? (
          <div key={`g-${x.inst_id}`} className="bt-notice is-warning" role="alert" data-testid="bt-gap">
            <SquareDashed aria-hidden="true" strokeWidth={1.75} />
            <div>
              <p className="bt-notice-title">{t('r.gap')}</p>
              <p className="bt-notice-body">
                {fill(t('r.gapBody'), { inst: <Fig>{x.inst_id}</Fig>, notes: <bdi>{x.gap_notes.join(' ')}</bdi> })}
              </p>
            </div>
          </div>
        ) : (
          <div key={`n-${x.inst_id}`} className="bt-notice" role="note" data-testid="bt-notes">
            <FileWarning aria-hidden="true" strokeWidth={1.75} />
            <div>
              <p className="bt-notice-title">{t('r.notes')}</p>
              <p className="bt-notice-body">
                <Fig>{x.inst_id}</Fig>
                {': '}
                <bdi>{x.gap_notes.join(' ')}</bdi>
              </p>
            </div>
          </div>
        ),
      )}
      {result.excluded.map((e) => (
        <p key={`x-${e.inst_id}`} className="bt-excl" data-testid="bt-excluded">
          <CircleMinus aria-hidden="true" strokeWidth={1.75} />
          <span>{fill(t('r.excluded'), { inst: <Fig>{e.inst_id}</Fig>, reason: <bdi>{e.reason}</bdi> })}</span>
        </p>
      ))}
    </>
  )
}

/** The result header line under the rule name: when it ran, on which candles, and the fee used. */
export function ResultMeta({ result, finishedAt, inst }: { result: BacktestResult; finishedAt: string | null; inst?: InstrumentResult }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const envKey = result.env === 'live' ? 'live' : 'demo'
  return (
    <p className="bt-meta" data-testid="bt-meta">
      {fill(t('r.ran'), {
        when: <span className="tabular-nums">{finishedAt ? formatDateTime(finishedAt, lang) : '—'}</span>,
        env: t(`r.env.${envKey}`),
        asof: <span className="tabular-nums">{result.data_as_of ? formatDateTime(result.data_as_of, lang) : '—'}</span>,
      })}{' '}
      {inst &&
        fill(t('r.fees'), {
          rate: <Fig>{`${formatFixed(Number(inst.taker_fee_rate) * 100, 2, lang)}%`}</Fig>,
          src: t(`r.src.${inst.fee_source === 'config_fallback' ? 'config_fallback' : 'config'}`),
        })}
    </p>
  )
}

interface ResultsProps {
  raw: unknown
  finishedAt: string | null
  /** The page head (rule name, status, the rule sentence), given the result line to sit under the name. */
  top: (meta: ReactNode) => ReactNode
}

export function Results({ raw, finishedAt, top }: ResultsProps) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const result = asResult(raw)
  const [chosen, setChosen] = useState('0')
  if (!result || result.instruments.length === 0) {
    return (
      <>
        {top(result ? <ResultMeta result={result} finishedAt={finishedAt} /> : null)}
        {result && <Notices result={result} lang={lang} />}
        <section className="bt-panel bt-fail-p" role="status" data-testid="bt-unreadable">
          <h3 className="text-title-3">{t('r.unreadable')}</h3>
          <p className="text-subheadline text-label-secondary">{t('r.unreadableBody')}</p>
        </section>
      </>
    )
  }
  const idx = Math.min(Number(chosen) || 0, result.instruments.length - 1)
  const inst = result.instruments[idx]!
  return (
    <>
      {top(<ResultMeta result={result} finishedAt={finishedAt} inst={inst} />)}
      <div className="bt-results" data-testid="bt-results">
      <Notices result={result} lang={lang} />
      {result.instruments.length > 1 && (
        <div className="bt-switch">
          <SegmentedControl
            aria-label={t('r.inst')}
            value={String(idx)}
            onValueChange={setChosen}
            options={result.instruments.map((x, i) => ({ value: String(i), label: <bdi dir="ltr">{base(x.inst_id)}</bdi> }))}
          />
        </div>
      )}
      <Tiles inst={inst} lang={lang} />
      <EquityCurve key={inst.inst_id} inst={inst} lang={lang} />
      <FoldsTable inst={inst} lang={lang} />
      <TradesTable key={`tr-${inst.inst_id}`} inst={inst} lang={lang} />
      </div>
    </>
  )
}

