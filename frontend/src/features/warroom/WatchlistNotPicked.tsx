import { Pin } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Spinner } from '../../ui'
import { Inline, InstrumentId } from './text'
import type { Pick, WatchlistCard as Card } from './watchlist'

/* The card's "Not picked" section: the Scout's strongest rejected candidates, each pinnable while the window is open. */

export interface WatchlistNotPickedProps {
  rejected: Card['rejected']
  live: Pick[]
  onList: Set<string>
  open: boolean
  busy: Record<string, boolean>
  onPin: (instId: string) => void
}

export function WatchlistNotPicked({ rejected, live, onList, open, busy, onPin }: WatchlistNotPickedProps) {
  const { t } = useTranslation('warroom')
  if (rejected.length === 0) return null
  return (
    <section className="wl-rej" aria-label={t('wl.notPicked')} data-testid="wl-rejected">
      <h4 className="wl-sec">{t('wl.notPicked')}</h4>
      <ul>
        {rejected.map((r) => {
          const pinned = live.some((x) => x.instId === r.instId && x.pinned)
          let act: ReactNode = null
          if (pinned)
            act = (
              <span className="wl-pinned-s">
                <Pin aria-hidden="true" strokeWidth={1.5} />
                <span>{t('wl.pinnedBy')}</span>
              </span>
            )
          else if (open && !onList.has(r.instId))
            act = busy[r.instId] ? (
              <span className="wl-busy">
                <Spinner />
              </span>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                className="wl-pin-one"
                icon={<Pin aria-hidden="true" strokeWidth={1.5} />}
                aria-label={t('wl.pinAria', { inst: r.instId })}
                onClick={() => onPin(r.instId)}
                data-testid="wl-pin-rejected"
              >
                {t('wl.pin')}
              </Button>
            )
          return (
            <li key={r.instId} data-testid="wl-rejected-row">
              <span className="wl-inst">
                <InstrumentId id={r.instId} />
              </span>
              <span className="wl-why" dir="auto">
                <Inline text={r.reason} />
              </span>
              <span className="wl-ract">{act}</span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
