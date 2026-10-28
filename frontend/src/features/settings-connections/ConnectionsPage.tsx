import { Lock, Plug } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Skeleton, Toggle } from '../../ui'
import { useConnection } from '../onboarding'
import { ConnectedView } from './ConnectedView'
import { LoadError, NotConnected } from './states'
import './settings.css'

/*
 * Settings → Connections (brief §14.2, design js/pages/settings.js `connections`): the OKX demo
 * account card with Test again / Replace keys / Remove…, its account rows, and the disabled
 * OKX live row. Keys are only ever shown as •••• last4.
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

function LiveGroup() {
  const { t } = useTranslation('settings')
  const labelId = useId()
  return (
    <section className="grid" aria-labelledby={`${labelId}-title`}>
      <h2 id={`${labelId}-title`} className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
        {t('conn.liveTitle')}
      </h2>
      <div role="list" className="st-group">
        <div role="listitem" aria-disabled="true" data-testid="live-row" className="flex min-h-12 items-center gap-3 px-4 py-2.5">
          <span
            aria-hidden="true"
            className="grid size-7 shrink-0 place-items-center rounded-[7px] bg-fill-3 text-label-secondary [&_svg]:size-4"
          >
            <Lock strokeWidth={1.5} />
          </span>
          <span className="grid min-w-0 flex-1 gap-px">
            <span id={labelId} className="text-callout text-label-secondary">
              {t('conn.live')}
            </span>
            <span className="text-footnote text-label-secondary">{t('conn.liveNote')}</span>
          </span>
          <Toggle checked={false} disabled aria-labelledby={labelId} />
        </div>
      </div>
    </section>
  )
}

export function ConnectionsPage() {
  const query = useConnection('demo')
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
    body = <ConnectedView key={query.connection.id} connection={query.connection} onRemoved={() => setRemoved(true)} />
  } else {
    body = <NotConnected focusOnMount={removed} />
  }

  return (
    <div className="st-page">
      <PaneHeader />
      {body}
      <LiveGroup />
    </div>
  )
}
