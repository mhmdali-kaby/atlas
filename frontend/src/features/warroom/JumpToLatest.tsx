import { ArrowDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'

/*
 * NewMessagesPill and ScrollToLatest (design v4 War Room §7): while scrolled up, new messages show
 * the accent pill "N new messages ↓"; scrolled up with nothing new, a round glass arrow-down button.
 * Rendered beside the feed's scroller, inside .wr-pane.
 */
export function JumpToLatest({ unread, scrolledUp, onJump }: { unread: number; scrolledUp: boolean; onJump: () => void }) {
  const { t } = useTranslation('warroom')
  if (unread === 0 && !scrolledUp) return null
  return (
    <div className="wr-float">
      {unread > 0 ? (
        <button type="button" className="wr-pill" onClick={onJump} data-testid="wr-new-pill">
          {t('newMessages', { count: unread })}
          <ArrowDown aria-hidden="true" />
        </button>
      ) : (
        <button type="button" className="wr-latest" onClick={onJump} aria-label={t('latest')} title={t('latest')} data-testid="wr-latest">
          <ArrowDown aria-hidden="true" strokeWidth={1.5} />
        </button>
      )}
    </div>
  )
}
