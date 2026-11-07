import { Plug } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Skeleton } from '../../ui'
import { useConnection } from '../onboarding'
import { ConnectedView } from './ConnectedView'
import { LiveSection } from './LiveSection'
import { LoadError, NotConnected } from './states'
import './settings.css'

/*
 * Settings → Connections (brief §14.2, design js/pages/settings.js `connections`): the OKX demo
 * account card with Test again / Replace keys / Remove…, its account rows, and the OKX live
 * account under it (ATL-241). Keys are only ever shown as •••• last4.
 */

function PaneHeader() {
  const { t } = useTranslation('settings')
  return (
    <header className="st-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Plug strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav.connections')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('conn.intro')}</p>
      </div>
    </header>
  )
}

export function ConnectionsPage() {
  const query = useConnection('demo')
  const live = useConnection('live').connection
  const activeEnv = useTradingEnv()
  const [removed, setRemoved] = useState(false)

  let body
  if (query.isPending) {
    body = (
      <div className="grid gap-7" aria-busy="true" data-testid="connections-loading">
        <Skeleton className="h-[112px] rounded-card" />
        <Skeleton className="h-[240px] rounded-card" />
      </div>
    )
  } else if (query.isError && !query.data) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else if (query.connection) {
    body = (
      <ConnectedView
        key={query.connection.id}
        connection={query.connection}
        trading={activeEnv === 'demo' && live !== null}
        onRemoved={() => setRemoved(true)}
      />
    )
  } else {
    body = <NotConnected focusOnMount={removed} />
  }

  return (
    <div className="st-page">
      <PaneHeader />
      {body}
      {query.data && <LiveSection connection={live} />}
    </div>
  )
}
