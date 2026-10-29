import { ArrowLeftRight, ChartCandlestick, Check, CircleAlert, Coins, Layers, Scale } from 'lucide-react'
import type { CSSProperties, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Spinner } from '../../ui'
import type { StorageReport } from './api'
import { bytesText, CANDLE_KEYS, compact, pctText, type DatasetKey, type DatasetTotal, type Model } from './model'
import { Fig, NS, Tr, useDepthText } from './parts'

/* What Atlas keeps (design §5.5): each dataset's depth, progress and the storage its table uses. */

function Meter({ p, cls }: { p: number; cls?: string }) {
  return (
    <span className={`dt-meter${cls ? ` ${cls}` : ''}`}>
      <i style={{ '--p': p.toFixed(4) } as CSSProperties} />
    </span>
  )
}

function OpenMeter({ x }: { x: DatasetTotal }) {
  return <Meter p={x.all ? 1 : 0} cls={x.all ? undefined : x.running ? 'is-indeterminate' : x.paused ? 'is-paused' : 'is-waiting'} />
}

function OpenPct({ x }: { x: DatasetTotal }) {
  const { t } = useTranslation(NS)
  if (x.all)
    return (
      <span className="dt-keep-pct">
        <Check aria-hidden="true" />
        <span className="sr-only">{t('st.done')}</span>
      </span>
    )
  if (x.running)
    return (
      <span className="dt-keep-pct">
        <Spinner className="dt-spin" />
        <span className="sr-only">{t('st.fetching')}</span>
      </span>
    )
  return <span className="dt-keep-pct text-label-secondary">—</span>
}

function Row({ children, child, testId }: { children: ReactNode; child?: boolean; testId?: string }) {
  return (
    <div className={`dt-keep-row${child ? ' is-child' : ''}`} data-testid={testId}>
      {children}
    </div>
  )
}

export function Keeps({ model: m, storage, depthOf }: { model: Model; storage: StorageReport | undefined; depthOf: (k: DatasetKey) => number | null }) {
  const { t } = useTranslation(NS)
  const depthText = useDepthText()
  const size = (name: string) => storage?.hypertables.find((h) => h.name === name)?.bytes ?? null
  const sizeCell = (name: string, quiet = false) => {
    const b = size(name)
    return <span className={`dt-keep-size${quiet ? ' is-quiet' : ''}`}>{b === null ? '—' : <Fig>{bytesText(b)}</Fig>}</span>
  }

  let cw = 0
  let ce = 0
  for (const k of CANDLE_KEYS) {
    cw += m.datasets[k].written
    ce += m.datasets[k].expected
  }
  const cp = ce > 0 ? cw / ce : 0

  const openRow = (key: 'funding' | 'open_interest', table: string, Icon: typeof Coins) => {
    const x = m.datasets[key]
    return (
      <Row testId={`dt-keep-${key}`}>
        <span className="dt-keep-name">
          <span className="dt-row-icon" aria-hidden="true">
            <Icon strokeWidth={1.75} />
          </span>
          <span>{t(`ds.${key}`)}</span>
        </span>
        <span className="dt-keep-depth">{depthText(key, null)}</span>
        <OpenMeter x={x} />
        <OpenPct x={x} />
        <span className="dt-keep-count">
          <span>
            <Tr k="records" count={x.written} values={{ n: compact(x.written) }} />
          </span>
        </span>
        {sizeCell(table)}
      </Row>
    )
  }
  const offRow = (label: string, table: string, Icon: typeof Coins) => (
    <Row>
      <span className="dt-keep-name">
        <span className="dt-row-icon" aria-hidden="true">
          <Icon strokeWidth={1.75} />
        </span>
        <span>{t(label)}</span>
      </span>
      <span className="dt-keep-off">{t('k.off')}</span>
      {sizeCell(table, true)}
    </Row>
  )

  return (
    <section className="dt-section" aria-labelledby="dt-k-title" data-testid="dt-keeps">
      <div className="dt-sec-head">
        <h2 className="dt-group-title" id="dt-k-title">
          {t('k.title')}
        </h2>
        <span className="dt-sec-aside" data-testid="dt-storage-total">
          {storage ? <Tr k="k.onMac" values={{ size: bytesText(storage.total_bytes) }} /> : null}
        </span>
      </div>
      <div className="dt-group dt-keep">
        <Row testId="dt-keep-candles">
          <span className="dt-keep-name">
            <span className="dt-row-icon" aria-hidden="true">
              <ChartCandlestick strokeWidth={1.75} />
            </span>
            <span>
              {t('k.candles')}
              <small>{t('k.candlesSub')}</small>
            </span>
          </span>
          <span />
          <Meter p={cp} />
          <span className="dt-keep-pct">
            <Fig>{pctText(cp)}</Fig>
          </span>
          <span className="dt-keep-count">
            <span>
              <Tr k="bars" count={cw} values={{ n: compact(cw) }} />
            </span>
          </span>
          {sizeCell('candles')}
        </Row>
        {CANDLE_KEYS.map((key) => {
          const x = m.datasets[key]
          return (
            <Row child key={key} testId={`dt-keep-${key}`}>
              <span className="dt-keep-name">{t(`ds.${key}`)}</span>
              <span className="dt-keep-depth">{depthText(key, depthOf(key))}</span>
              <Meter p={x.pct} cls={x.paused > 0 && !x.all ? 'is-paused' : undefined} />
              <span className="dt-keep-pct">
                <Fig>{pctText(x.pct)}</Fig>
              </span>
              <span className="dt-keep-count">
                <span>
                  <Tr k="bars" count={x.written} values={{ n: compact(x.written) }} />
                </span>
                {x.failed > 0 && (
                  <span className="dt-keep-fail">
                    <CircleAlert aria-hidden="true" />
                    <Tr k="k.failed" values={{ n: x.failed }} />
                  </span>
                )}
              </span>
              <span />
            </Row>
          )
        })}
        {openRow('funding', 'funding_rates', Coins)}
        {openRow('open_interest', 'open_interest', Layers)}
        {offRow('k.ls', 'long_short_ratio', Scale)}
        {offRow('k.taker', 'taker_volume', ArrowLeftRight)}
      </div>
    </section>
  )
}
