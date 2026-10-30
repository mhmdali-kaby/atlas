import { Pin, SquarePen, TriangleAlert } from 'lucide-react'
import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button } from '../../ui'
import { Fig } from './historianParts'
import { PinPopover, type PinPopoverProps } from './PinPopover'
import { fillSlots, slot } from './slots'
import { InstrumentId } from './text'
import { mmss, type Conflict } from './watchlist'
import { css, Time } from './watchlistFormat'

/*
 * The edit window's band (design v9 §A6–A7): the close time, the live countdown, the draining bar
 * and Pin instrument with its popover. And the notice that replaces it after a 409.
 */

const LAST_MINUTE_MS = 60_000

export interface WatchlistEditBandProps extends Omit<PinPopoverProps, 'onClose'> {
  endsAt: string
  /** Milliseconds until the window closes. */
  left: number
  /** Share of the window still left when the band mounted (0..1), where the bar starts draining. */
  drainFrom: number | string
  lang: Lang
  pinRef: RefObject<HTMLButtonElement | null>
  popOpen: boolean
  onTogglePop: () => void
  onClosePop: () => void
}

export function WatchlistEditBand({ endsAt, left, drainFrom, lang, pinRef, popOpen, onTogglePop, onClosePop, ...popover }: WatchlistEditBandProps) {
  const { t } = useTranslation('warroom')
  return (
    <div className="wl-band-wrap">
      <div className={`wl-band${left <= LAST_MINUTE_MS ? ' is-last' : ''}`} data-testid="wl-band">
        <span className="wl-band-ic">
          <SquarePen aria-hidden="true" strokeWidth={1.5} />
        </span>
        <p className="wl-band-t">{fillSlots(t('wl.window', { time: slot('time') }), { time: <Time ts={endsAt} lang={lang} /> })}</p>
        <span className="wl-left" data-testid="wl-countdown">
          {fillSlots(t('wl.left', { t: slot('t') }), { t: <Fig>{mmss(left)}</Fig> })}
        </span>
        <Button
          ref={pinRef}
          size="sm"
          variant="secondary"
          className="wl-pin-btn"
          icon={<Pin aria-hidden="true" strokeWidth={1.5} />}
          aria-haspopup="dialog"
          aria-expanded={popOpen}
          onClick={onTogglePop}
          data-wl-pin-trigger=""
          data-testid="wl-pin-instrument"
        >
          {t('wl.pinBtn')}
        </Button>
        <span className="wl-drain" aria-hidden="true" style={css({ '--from': drainFrom, animationDuration: `${Math.max(0, left)}ms` })} />
      </div>
      {popOpen && (
        <PinPopover
          {...popover}
          onClose={(back) => {
            onClosePop()
            if (back) pinRef.current?.focus()
          }}
        />
      )}
    </div>
  )
}

/** Shown instead of the band once a write got 409: the window closed, or a newer version replaced this one. */
export function WatchlistConflict({ conflict }: { conflict: Conflict }) {
  const { t } = useTranslation('warroom')
  const replaced = conflict.error === 'not_current_version'
  return (
    <div className="wl-conflict" role="alert" tabIndex={-1} data-testid="wl-conflict">
      <TriangleAlert aria-hidden="true" strokeWidth={1.5} />
      <div>
        <p className="wl-conflict-t">{t(replaced ? 'wl.replacedTitle' : 'wl.conflictTitle')}</p>
        <p>
          {fillSlots(t(`${replaced ? 'wl.replacedConflict' : 'wl.conflict'}.${conflict.kind}`, { inst: slot('inst') }), {
            inst: <InstrumentId id={conflict.instId} />,
          })}
        </p>
      </div>
    </div>
  )
}
