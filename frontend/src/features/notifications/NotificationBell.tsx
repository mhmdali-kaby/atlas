import { Bell, BellOff, BellRing, CheckCheck, CircleDashed, CloudOff, MonitorDot, RefreshCw, Settings, Timer } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { toolbarIconButton } from '../../app/shell/toolbarClasses'
import { ToolbarGroup } from '../../app/shell/Prefs'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import type { Lang } from '../../lib/format'
import { Button, SegmentedControl, Skeleton, toast, useToasts } from '../../ui'
import { cx } from '../../ui/cx'
import { OPEN_NOTIFICATIONS_EVENT } from '../../ui/toastStore'
import { openForApproval, useAwaitingProposals } from '../approvals/api'
import { useLang, useNow } from '../portfolio/hooks'
import { baghdadDay, formatDate, formatTime, mmss } from '../warroom/time'
import { rowsOf, unreadCountOf, useMarkAllRead, useMarkRead, useNotificationEvents, useNotificationList, type NotificationRow } from './api'
import { showBrowserNotification, useBrowserPermission } from './browser'
import { ltr, plainOf, TILE_CLASS, toastDuration, viewOf } from './view'
import './notifications.css'

/*
 * The toolbar bell and its list (ATL-217, design v18 §5.3–5.5). Every `notification.created` takes
 * one path: the row joins the list and the unread count, the bell swings once, a toast shows when
 * its In Atlas switch was on, and a browser banner when its Browser switch was on, permission is
 * granted and Atlas is not in front. Rows already there when the app opens are never toasted.
 */

const DAY_MS = 86_400_000

/** Opens a row: marks it read, closes the list and its toast, and goes to its link. */
function useOpenRow(onClose: () => void) {
  const env = useTradingEnv()
  const navigate = useNavigate()
  const markRead = useMarkRead(env)
  const dismissTag = useToasts((s) => s.dismissTag)
  return useCallback(
    (row: NotificationRow) => {
      if (!row.read_at) markRead.mutate(row.id)
      dismissTag(row.id)
      onClose()
      navigate(row.link)
    },
    // markRead.mutate is stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [navigate, dismissTag, onClose],
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
          <Panel rows={rows} unread={unread} list={list} lang={lang} onOpenRow={openRow} onClose={close} />
        </Popover.Portal>
      </Popover.Root>
    </ToolbarGroup>
  )
}

function Panel({
  rows,
  unread,
  list,
  lang,
  onOpenRow,
  onClose,
}: {
  rows: NotificationRow[]
  unread: number
  list: ReturnType<typeof useNotificationList>
  lang: Lang
  onOpenRow: (row: NotificationRow) => void
  onClose: () => void
}) {
  const { t } = useTranslation('notifications')
  const env = useTradingEnv()
  const markAll = useMarkAllRead(env)
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const title = useRef<HTMLHeadingElement>(null)
  const ready = list.status === 'success'

  return (
    <Popover.Content
      className="nt-panel"
      align="end"
      sideOffset={8}
      collisionPadding={8}
      aria-labelledby="nt-panel-title"
      data-testid="nt-panel"
      onOpenAutoFocus={(e) => {
        e.preventDefault()
        title.current?.focus()
      }}
    >
      <div className="nt-head">
        <h2 ref={title} id="nt-panel-title" tabIndex={-1} className="nt-head-title">
          {t('title')}
        </h2>
        <Button
          variant="plain"
          size="sm"
          icon={<CheckCheck aria-hidden="true" strokeWidth={1.5} />}
          disabled={!ready || unread === 0}
          onClick={() => markAll.mutate()}
          data-testid="nt-markall"
        >
          {t('markAll')}
        </Button>
        <Link to="/settings/notifications" className={cx(toolbarIconButton, 'nt-gear')} aria-label={t('settings')} title={t('settings')} onClick={onClose}>
          <Settings aria-hidden="true" strokeWidth={1.5} />
        </Link>
      </div>
      {ready && rows.length > 0 && (
        <div className="nt-filter-row">
          <SegmentedControl
            aria-label={t('title')}
            value={filter}
            onValueChange={setFilter}
            options={[
              { value: 'all', label: t('all') },
              { value: 'unread', label: unread ? `${t('unread')} ${unread}` : t('unread') },
            ]}
          />
        </div>
      )}
      <PermissionPrompt />
      <div className="nt-scroll">
        <Body rows={rows} filter={filter} list={list} lang={lang} onOpenRow={onOpenRow} onClose={onClose} />
      </div>
    </Popover.Content>
  )
}

function Body({
  rows,
  filter,
  list,
  lang,
  onOpenRow,
  onClose,
}: {
  rows: NotificationRow[]
  filter: 'all' | 'unread'
  list: ReturnType<typeof useNotificationList>
  lang: Lang
  onOpenRow: (row: NotificationRow) => void
  onClose: () => void
}) {
  const { t } = useTranslation('notifications')
  const now = useNow(60_000)

  if (list.status === 'pending') {
    return (
      <ul className="nt-list" aria-busy="true" aria-label={t('title')}>
        {Array.from({ length: 5 }, (_, i) => (
          <li key={i} className="nt-sk">
            <Skeleton className="size-8 rounded-[9px]" />
            <span className="grid flex-1 gap-1.5">
              <Skeleton className="h-[13px] w-[62%]" />
              <Skeleton className="h-[11px] w-[88%]" />
            </span>
          </li>
        ))}
      </ul>
    )
  }
  if (list.status === 'error') {
    return (
      <div className="nt-state" role="alert">
        <span className="nt-state-ic is-error">
          <CloudOff aria-hidden="true" strokeWidth={1.5} />
        </span>
        <p className="nt-state-title">{t('error.title')}</p>
        <p className="nt-state-body">{t('error.body')}</p>
        <Button size="sm" icon={<RefreshCw aria-hidden="true" strokeWidth={1.5} />} onClick={() => void list.refetch()}>
          {t('retry')}
        </Button>
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="nt-state">
        <span className="nt-state-ic">
          <BellOff aria-hidden="true" strokeWidth={1.5} />
        </span>
        <p className="nt-state-title">{t('empty.title')}</p>
        <p className="nt-state-body">{t('empty.body')}</p>
        <Button size="sm" asChild>
          <Link to="/settings/notifications" onClick={onClose}>
            {t('empty.link')}
          </Link>
        </Button>
      </div>
    )
  }
  const shown = filter === 'unread' ? rows.filter((r) => !r.read_at) : rows
  if (shown.length === 0) {
    return (
      <div className="nt-state">
        <span className="nt-state-ic is-ok">
          <CheckCheck aria-hidden="true" strokeWidth={1.5} />
        </span>
        <p className="nt-state-title">{t('emptyUnread.title')}</p>
        <p className="nt-state-body">{t('emptyUnread.body')}</p>
      </div>
    )
  }

  const days: { key: string; label: string; rows: NotificationRow[] }[] = []
  for (const row of shown) {
    const key = baghdadDay(row.created_at)
    let day = days.find((d) => d.key === key)
    if (!day) {
      const label = key === baghdadDay(now) ? t('today') : key === baghdadDay(now - DAY_MS) ? t('yesterday') : formatDate(row.created_at, lang)
      day = { key, label, rows: [] }
      days.push(day)
    }
    day.rows.push(row)
  }

  return (
    <div data-testid="nt-list">
      {days.map((d) => (
        <section key={d.key} className="nt-day" aria-label={d.label}>
          <h3 className="nt-day-title">{d.label}</h3>
          <ul className="nt-list">
            {d.rows.map((row) => (
              <Item key={row.id} row={row} lang={lang} onOpen={onOpenRow} />
            ))}
          </ul>
        </section>
      ))}
      {list.hasNextPage ? (
        <div className="nt-end">
          <Button size="sm" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()} data-testid="nt-older">
            {t('older')}
          </Button>
        </div>
      ) : (
        <p className="nt-end">{t('end')}</p>
      )}
    </div>
  )
}

function Item({ row, lang, onOpen }: { row: NotificationRow; lang: Lang; onOpen: (row: NotificationRow) => void }) {
  const { t } = useTranslation('notifications')
  const v = viewOf(row, t, lang)
  const Icon = v.icon
  const unread = !row.read_at
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    onOpen(row)
  }
  return (
    <li className={cx('nt-item', unread && 'is-unread')} data-testid="nt-row" data-id={row.id} data-unread={unread || undefined}>
      <a className="nt-row" href={row.link} onClick={click}>
        <span aria-hidden="true" className={cx('nt-tile', TILE_CLASS[v.tone])}>
          <Icon strokeWidth={1.5} />
        </span>
        <span className="nt-main">
          <span className="nt-title">
            {unread && <span className="sr-only">{t('unreadSr')} </span>}
            {v.title}
          </span>
          <span className="nt-body">{v.quote ? <q dir="auto" className="nt-headline">{v.body}</q> : v.body}</span>
          {v.meta && <span className="nt-meta-line">{v.meta}</span>}
          {row.event === 'proposal_awaiting_approval' && <LiveLine row={row} />}
        </span>
        <span className="nt-side">
          <time className="nt-time" dateTime={row.created_at}>
            {ltr(formatTime(row.created_at, lang))}
          </time>
          {unread && <i className="nt-dot" aria-hidden="true" />}
        </span>
      </a>
    </li>
  )
}

/** A waiting trade's live line: "5:54 left" (orange in the last minute), then "No longer waiting". */
function LiveLine({ row }: { row: NotificationRow }) {
  const { t } = useTranslation('notifications')
  const env = useTradingEnv()
  const now = useNow(1000)
  const awaiting = useAwaitingProposals(env)
  const id = typeof row.params.proposal_id === 'string' ? row.params.proposal_id : ''
  const end = typeof row.params.approval_expires_at === 'string' ? Date.parse(row.params.approval_expires_at) : NaN
  const open = useMemo(() => openForApproval(awaiting.data?.data, now), [awaiting.data, now])
  const left = end - now
  const waiting = Number.isFinite(end) && left > 0 && (!awaiting.data || open.some((p) => p.id === id))
  if (!waiting) {
    return (
      <span className="nt-live is-over">
        <CircleDashed aria-hidden="true" strokeWidth={1.75} />
        <span>{t('noLonger')}</span>
      </span>
    )
  }
  return (
    <span className={cx('nt-live', left < 60_000 && 'is-last')}>
      <Timer aria-hidden="true" strokeWidth={1.75} />
      <span>{t('left', { t: ltr(mmss(left)) })}</span>
    </span>
  )
}

/** "Get alerts when Atlas is in the background": only while permission is undecided, never on load. */
function PermissionPrompt() {
  const { t } = useTranslation('notifications')
  const { permission, hidden, ask, hide } = useBrowserPermission()
  const [asking, setAsking] = useState(false)
  if (hidden || permission === 'granted' || permission === 'unsupported') return null
  if (permission === 'denied') {
    return (
      <div className="nt-perm is-denied" role="note" data-testid="nt-perm">
        <BellOff aria-hidden="true" strokeWidth={1.5} />
        <div>
          <p className="nt-perm-title">{t('perm.denied.title')}</p>
          <p className="nt-perm-body">{t('perm.denied.body')}</p>
        </div>
        <Button variant="plain" size="sm" onClick={hide}>
          {t('perm.hide')}
        </Button>
      </div>
    )
  }
  return (
    <div className="nt-perm" role="note" data-testid="nt-perm">
      <MonitorDot aria-hidden="true" strokeWidth={1.5} />
      <div>
        <p className="nt-perm-title">{t('perm.title')}</p>
        <p className="nt-perm-body">{t('perm.body')}</p>
        <div className="nt-perm-actions">
          <Button
            variant="primary"
            size="sm"
            loading={asking}
            onClick={async () => {
              setAsking(true)
              try {
                const answer = await ask()
                if (answer === 'granted') toast({ title: t('perm.okToast'), body: t('perm.okBody') })
                else if (answer === 'denied') toast({ title: t('perm.noToast'), body: t('perm.noBody'), tone: 'info' })
              } finally {
                setAsking(false)
              }
            }}
          >
            {asking ? t('perm.asking') : t('perm.allow')}
          </Button>
          <Button variant="plain" size="sm" onClick={hide}>
            {t('perm.later')}
          </Button>
        </div>
      </div>
    </div>
  )
}
