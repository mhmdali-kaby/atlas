import { AppWindow, ChevronsUpDown, CircleSlash, MonitorDot, Send } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AppIcon } from '../../ui'
import { cx } from '../../ui/cx'
import { EVENTS, prefKey, type Channel, type EventId, type Permission, type Prefs } from './model'
import { EventTile } from './parts'

/*
 * The preview (design §6.2): what the chosen event looks like as a toast, a macOS banner and a
 * Telegram message, built from illustrative sample text. A stage whose channel is off is dimmed
 * and says why. The stages are pictures (aria-hidden); the matrix carries the real state.
 */

const NS = 'settings-notifications'
const LINKS: Record<EventId, string> = {
  proposal_awaiting_approval: '/warroom?proposal=P-0412',
  circuit_breaker: '/warroom',
  kill_switch: '/warroom',
  reconciliation_mismatch: '/portfolio/positions',
  order_filled: '/portfolio/orders?inst_id=AAPL-USDC-SWAP',
  stop_hit: '/portfolio/positions?inst_id=NVDA-USDT-SWAP',
  target_hit: '/portfolio/positions?inst_id=ETH-USDT-SWAP',
  news_alert: '/warroom?message=m1',
  agent_degraded: '/agents',
  data_stale: '/settings/data',
  daily_report: '/warroom?message=m1',
}
const CH_ICON = { in_app: AppWindow, browser: MonitorDot, telegram: Send }

export function Preview({
  selected,
  onSelect,
  prefs,
  permission,
  telegramReady,
}: {
  selected: EventId
  onSelect: (e: EventId) => void
  prefs: Prefs
  permission: Permission
  telegramReady: boolean
}) {
  const { t } = useTranslation(NS)
  const ev = EVENTS.find((e) => e.id === selected) ?? EVENTS[0]
  const s = (k: string) => t(`sample.${selected}.${k}`, { defaultValue: '' })
  const critical = ev.tone === 'critical'
  const on = (ch: Channel) => prefs[prefKey(selected, ch)] === true

  const offWhy = (ch: Channel): string | null => {
    if (ch === 'telegram' && !telegramReady) return t('pv.notSetUp')
    if (ch === 'browser' && permission === 'denied') return t('pv.blocked')
    return on(ch) ? null : t('pv.off')
  }

  const card = (ch: Channel, stage: React.ReactNode, desc: string) => {
    const Icon = CH_ICON[ch]
    const why = offWhy(ch)
    return (
      <div className={cx('ns-pv-card', why && 'is-off')} data-testid={`pv-${ch}`}>
        <p className="ns-pv-k">
          <Icon aria-hidden="true" strokeWidth={1.5} />
          <span>{t(`ch.${ch}`)}</span>
          {why && (
            <span className="ns-pv-off">
              <CircleSlash aria-hidden="true" strokeWidth={1.75} />
              <span>{why}</span>
            </span>
          )}
        </p>
        <div className="ns-pv-stage" aria-hidden="true">
          {stage}
        </div>
        <p className="ns-pv-desc">{desc}</p>
      </div>
    )
  }

  const tgText = `[DEMO] ${s('title')}\n${s('body')}\n${t('pv.open')}: http://localhost:3000${LINKS[selected]}`

  return (
    <aside className="ns-preview" aria-labelledby="ns-pv-title" data-testid="ns-preview">
      <div className="ns-pv-head">
        <h3 className="ns-pv-title" id="ns-pv-title">
          {t('preview')}
        </h3>
        <label className="ns-pv-pick">
          <span className="sr-only">{t('previewPick')}</span>
          <select value={selected} onChange={(e) => onSelect(e.target.value as EventId)} data-testid="pv-select">
            {EVENTS.map((x) => (
              <option key={x.id} value={x.id}>
                {t(`ev.${x.id}`)}
              </option>
            ))}
          </select>
          <ChevronsUpDown className="ns-pv-caret" aria-hidden="true" strokeWidth={1.5} />
        </label>
      </div>
      {card(
        'in_app',
        <div className={cx('ns-toast', critical && 'is-critical')}>
          <EventTile event={ev.id} tone={ev.tone} />
          <div className="ns-toast-main">
            <p className="ns-toast-head">
              <span dir="auto">{s('title')}</span>
              <time>{t('pv.now')}</time>
            </p>
            <p className="ns-toast-body" dir="auto">
              {s('body')}
            </p>
            {s('meta') && <p className="ns-toast-meta">{s('meta')}</p>}
            <span className="ns-toast-action">{s('action')}</span>
          </div>
        </div>,
        t('pv.inApp'),
      )}
      {card(
        'browser',
        <div className="ns-mac">
          <AppIcon size={30} />
          <div className="ns-mac-text">
            <p className="ns-mac-top">
              <b dir="auto">{s('title')}</b>
              <time>{t('pv.now')}</time>
            </p>
            <p className="ns-mac-body" dir="auto">
              {s('body')}
            </p>
            <p className="ns-mac-src" dir="ltr">
              localhost:3000
            </p>
          </div>
        </div>,
        t('pv.browser'),
      )}
      {card(
        'telegram',
        <div className="ns-tgchat">
          <div className="ns-tg-bubble">
            <p className="ns-tg-text" dir="auto">
              {tgText}
            </p>
          </div>
          {selected === 'proposal_awaiting_approval' && (
            <div className="ns-tg-keys">
              <span>{t('pv.approve')}</span>
              <span>{t('pv.reject')}</span>
            </div>
          )}
        </div>,
        selected === 'proposal_awaiting_approval' ? t('pv.telegramApprove') : t('pv.telegram'),
      )}
    </aside>
  )
}
