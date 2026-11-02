import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Skeleton } from '../../ui'
import { baghdadDay } from '../warroom/time'
import { useRun } from './api'
import { BacktestForm } from './BacktestForm'
import { fromRequest, initialForm } from './model'
import { NS } from './parts'
import '../warroom/warroom.css'
import './backtester.css'

/*
 * /backtester: a new backtest, or with ?from=<run id> ("Edit and run again") the form filled with
 * that run's request. Today is the Baghdad day, so the default period ends at 00:00 Baghdad today.
 */
export function BacktesterPage() {
  const { t } = useTranslation(NS)
  const env = useTradingEnv()
  const [params] = useSearchParams()
  const from = params.get('from')
  const source = useRun(env, from)
  const [today] = useState(() => baghdadDay(Date.now()))

  if (from && source.isPending) {
    return (
      <div className="bt-page" aria-busy="true" aria-label={t('new')}>
        <Skeleton style={{ width: 240, height: 32 }} />
        <Skeleton style={{ height: 96 }} />
        <Skeleton style={{ height: 320 }} />
      </div>
    )
  }
  const initial = from && source.data ? fromRequest(source.data.request) : initialForm(today)
  return <BacktestForm key={from ?? 'new'} initial={initial} />
}
