import { ArrowDownRight, ArrowUpRight, CloudOff, Layers, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { formatDecimal, formatPrice, type Lang } from '../../lib/format'
import { Button, SignedValue, Skeleton } from '../../ui'
import { usePositions, type Position } from '../portfolio/api'
import { useLang } from '../portfolio/hooks'
import type { WarroomThread } from './api'
import { InstrumentId } from './text'

/*
 * Open positions (design v4 War Room §7, ATL-43): the inspector's own section, in every
 * thread, sharing GET /api/portfolio/positions and its TanStack Query key with
 * Portfolio → Positions so the two never disagree. In an instrument thread, that
 * instrument's position (if any) is listed first, highlighted.
 */

function PositionRow({ p, lang, here }: { p: Position; lang: Lang; here: boolean }) {
  const { t } = useTranslation('warroom')
  const Icon = p.side === 'long' ? ArrowUpRight : ArrowDownRight
  return (
    <li>
      <Link
        to="/portfolio/positions"
        className={here ? 'pos-row is-here' : 'pos-row'}
        data-testid="wr-position-row"
        data-inst={p.inst_id}
      >
        <span className="pos-l1">
          <InstrumentId id={p.inst_id} />
          <SignedValue value={p.upl_usd} unit="USD" />
        </span>
        <span className="pos-l2">
          <Icon aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
          <span>
            <Trans
              t={t}
              i18nKey="positions.terms"
              values={{
                side: t(`positions.side.${p.side}`),
                lever: formatDecimal(p.leverage, lang, 0),
                contracts: formatDecimal(p.size, lang, 0),
              }}
              components={{ lever: <span className="num fig" />, n: <span className="num fig" /> }}
            />
          </span>
        </span>
        <span className="pos-l3">
          <span>
            {t('positions.entry')} <span className="num fig">{formatPrice(p.entry, lang)}</span>
          </span>
          <span>
            {t('positions.mark')} <span className="num fig">{formatPrice(p.mark, lang)}</span>
          </span>
        </span>
      </Link>
    </li>
  )
}

function PositionSkeleton() {
  return (
    <ul className="insp-pos" aria-busy="true" data-testid="wr-positions-loading">
      {[0, 1, 2].map((i) => (
        <li key={i} className="pos-skel">
          <Skeleton style={{ width: '55%', height: 12 }} />
          <Skeleton style={{ width: '75%', height: 10 }} />
          <Skeleton style={{ width: '65%', height: 10 }} />
        </li>
      ))}
    </ul>
  )
}

export function InspectorPositions({ env, thread }: { env: TradingEnv; thread: WarroomThread | undefined }) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const query = usePositions(env)

  const all = query.data?.data.items ?? []
  const here = thread?.kind === 'instrument' ? thread.key : null
  const list = here ? [...all.filter((p) => p.inst_id === here), ...all.filter((p) => p.inst_id !== here)] : all

  let body: ReactNode
  if (query.isPending) {
    body = <PositionSkeleton />
  } else if (query.isError) {
    body = (
      <div className="insp-empty is-error" role="alert" data-testid="wr-positions-error">
        <CloudOff aria-hidden="true" strokeWidth={1.5} />
        <p className="insp-empty-title">{t('positions.error.title')}</p>
        <p>{t('positions.error.body')}</p>
        <Button variant="secondary" size="sm" icon={<RefreshCw aria-hidden="true" />} onClick={() => void query.refetch()}>
          {t('tryAgain')}
        </Button>
      </div>
    )
  } else if (list.length === 0) {
    body = (
      <div className="insp-empty" data-testid="wr-positions-empty">
        <Layers aria-hidden="true" strokeWidth={1.5} />
        <p className="insp-empty-title">{t('positions.empty.title')}</p>
        <p>{t('positions.empty.body')}</p>
      </div>
    )
  } else {
    body = (
      <ul className="insp-pos" data-testid="wr-positions-list">
        {list.map((p) => (
          <PositionRow key={p.id} p={p} lang={lang} here={p.inst_id === here} />
        ))}
      </ul>
    )
  }

  return (
    <section className="insp-sec" aria-labelledby="wr-insp-positions" data-testid="wr-positions">
      <h3 className="insp-title" id="wr-insp-positions">
        <span>
          {t('positions.title')}
          {!query.isPending && !query.isError && list.length > 0 && <span className="num"> {formatDecimal(list.length, lang, 0)}</span>}
        </span>
        <Link to="/portfolio/positions">{t('positions.view')}</Link>
      </h3>
      {body}
    </section>
  )
}
