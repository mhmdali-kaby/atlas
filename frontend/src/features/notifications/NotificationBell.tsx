import { Bell, BellRing } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { toolbarIconButton } from '../../app/shell/toolbarClasses'
import { ToolbarGroup } from '../../app/shell/Prefs'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { toast, useToasts } from '../../ui'
import { cx } from '../../ui/cx'
import { OPEN_NOTIFICATIONS_EVENT } from '../../ui/toastStore'
import { useLang } from '../portfolio/hooks'
import { rowsOf, unreadCountOf, useMarkRead, useNotificationEvents, useNotificationList, type NotificationRow } from './api'
import { showBrowserNotification } from './browser'
import { LiveLine } from './LiveLine'
import { NotificationPanel } from './NotificationList'
import { plainOf, TILE_CLASS, toastDuration, viewOf } from './view'
import './notifications.css'

/*
 * The toolbar bell (ATL-217, design v18 §5.3–5.5). Every `notification.created` takes
 * one path: the row joins the list and the unread count, the bell swings once, a toast shows when
 * its In Atlas switch was on, and a browser banner when its Browser switch was on, permission is
 * granted and Atlas is not in front. Rows already there when the app opens are never toasted.
 */

/** Opens a row: marks it read, closes the list and its toast, and goes to its link. */
function useOpenRow(onClose: () => void) {
  const env = useTradingEnv()
  const navigate = useNavigate()
  const { mutate: markRead } = useMarkRead(env)
  const dismissTag = useToasts((s) => s.dismissTag)
  return useCallback(
    (row: NotificationRow) => {
      if (!row.read_at) markRead(row.id)
      dismissTag(row.id)
      onClose()
      navigate(row.link)
    },
    [markRead, navigate, dismissTag, onClose],
  )
}

export function NotificationBell() {
  const { t } = useTranslation('notifications')
  const lang = useLang()
  const env = useTradingEnv()
  const list = useNotificationList(env)
  const [open, setOpen] = useState(false)
  const [ring, setRing] = useState(0)
  const close = useCallback(() => setOpen(false), [])
  const openRow = useOpenRow(close)

  const receive = useCallback(
    (row: NotificationRow) => {
      setRing((r) => r + 1)
      const v = viewOf(row, t, lang)
      if (row.channels?.in_app !== false) {
        const Icon = v.icon
        toast({
          tag: row.id,
          tone: v.critical ? 'error' : 'info',
          title: v.title,
          body: v.quote ? <q dir="auto" className="nt-headline">{v.body}</q> : v.body,
          meta: v.meta || undefined,
          extra: row.event === 'proposal_awaiting_approval' ? <LiveLine row={row} /> : undefined,
          icon: <Icon aria-hidden="true" strokeWidth={1.5} />,
          tileClass: TILE_CLASS[v.tone],
          time: t('now'),
          action: { label: v.action, onSelect: () => openRow(row) },
          durationMs: toastDuration(row.severity),
          urgent: v.critical ? t('urgent') : undefined,
        })
      }
      if (row.channels?.browser === true) {
        showBrowserNotification({ ...plainOf(v), tag: row.id, urgent: v.critical }, () => openRow(row))
      }
    },
    [t, lang, openRow],
  )
  useNotificationEvents(env, receive)

  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener(OPEN_NOTIFICATIONS_EVENT, show)
    return () => window.removeEventListener(OPEN_NOTIFICATIONS_EVENT, show)
  }, [])

  const rows = rowsOf(list.data)
  const unread = unreadCountOf(list.data)
  const urgent = rows.filter((r) => !r.read_at && r.severity === 'critical').length
  const label = unread ? (urgent ? t('bellAriaUrgent', { n: unread, c: urgent }) : t('bellAria', { n: unread })) : t('bellNone')
  const Glyph = urgent ? BellRing : Bell

  return (
    <ToolbarGroup>
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger asChild>
          <button
            type="button"
            className={cx(toolbarIconButton, 'nt-bell relative')}
            aria-label={label}
            title={t('title')}
            data-testid="nt-bell"
          >
            <Glyph key={ring} aria-hidden="true" strokeWidth={1.5} className={ring ? 'nt-ring' : undefined} />
            {unread > 0 && (
              <span aria-hidden="true" data-testid="nt-badge" className={cx('nt-badge', urgent > 0 && 'is-urgent')}>
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <NotificationPanel rows={rows} unread={unread} list={list} lang={lang} onOpenRow={openRow} onClose={close} />
        </Popover.Portal>
      </Popover.Root>
    </ToolbarGroup>
  )
}
