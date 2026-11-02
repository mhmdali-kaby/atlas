import { BellOff, CheckCheck, CloudOff, RefreshCw, Settings } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useRef, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { toolbarIconButton } from '../../app/shell/toolbarClasses'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import type { Lang } from '../../lib/format'
import { Button, SegmentedControl, Skeleton } from '../../ui'
import { cx } from '../../ui/cx'
import { useNow } from '../portfolio/hooks'
import { baghdadDay, formatDate, formatTime } from '../warroom/time'
import { useMarkAllRead, type NotificationRow, type useNotificationList } from './api'
import { iso } from './events'
import { LiveLine } from './LiveLine'
import { PermissionPrompt } from './PermissionPrompt'
import { TILE_CLASS, viewOf } from './view'

/*
 * The notifications list (design v18 §5.3): the popover panel under the toolbar bell with its
 * header, All/Unread filter, permission prompt, and the rows grouped by Baghdad day, newest first,
 * with loading, error, empty and unread-empty states.
 */

const DAY_MS = 86_400_000

export function NotificationPanel({
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
            {iso(formatTime(row.created_at, lang))}
          </time>
          {unread && <i className="nt-dot" aria-hidden="true" />}
        </span>
      </a>
    </li>
  )
}
