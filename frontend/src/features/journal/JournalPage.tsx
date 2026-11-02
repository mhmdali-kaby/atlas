import { CloudOff, FileText, Layers, NotebookPen, RefreshCw, SearchX } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Button, Skeleton, Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import { useLang, useNow } from '../portfolio/hooks'
import { formatDate } from '../warroom/time'
import { filterQuery, isFiltered, readFilters, useJournal, useJournalEntry, type JournalEntry, type JournalFilters } from './api'
import { JournalEntryView } from './JournalEntryView'
import { JournalFilterBar } from './JournalFilters'
import { JournalRow } from './JournalRow'
import { closedAt, isoMs, weekEnd } from './model'
import '../portfolio/portfolio.css'
import '../warroom/warroom.css'
import './journal.css'

/*
 * The Journal (brief §5 journal_entries, §16 /api/journal; design §7, ATL-202). A Mail-style split
 * view: post-mortems newest first, grouped by review week (Sunday 18:00 to Sunday 18:00 Baghdad),
 * with infinite scroll on ATL-200's cursor; the open one in full beside it. The filters and the open
 * entry live in the URL (`inst`, `agent`, `tag`, `outcome`, `range`, `from`, `to`, `entry`), so a
 * reload or a shared link lands in the same place. A filter change replaces the history entry;
 * opening an entry on a narrow window pushes one, so Back returns to the list.
 */

const NARROW = '(max-width: 899px)'

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(NARROW).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(NARROW)
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return narrow
}

function State({ kind, icon: Icon, title, body, action }: { kind: string; icon: typeof CloudOff; title: string; body: string; action?: ReactNode }) {
  const parts = body.split('./atlas up')
  return (
    <div className={cx('jr-state', `is-${kind}`)} role={kind === 'error' ? 'alert' : 'status'} data-testid={`journal-${kind}`}>
      <span className="jr-state-icon">
        <Icon aria-hidden="true" strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{title}</h2>
      <p className="jr-state-body">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code dir="ltr" className="jr-cmd">
              ./atlas up
            </code>
            {parts[1]}
          </>
        ) : (
          body
        )}
      </p>
      {action}
    </div>
  )
}

function SkeletonRows() {
  return (
    <>
      {[150, 128, 160, 140, 120, 150, 136].map((w, i) => (
        <div key={i} className="jr-row is-skel" aria-hidden="true">
          <Skeleton className="size-[22px] rounded-full" />
          <span className="grid flex-1 gap-[7px]">
            <Skeleton style={{ width: w, height: 14 }} />
            <Skeleton style={{ width: 120, height: 11 }} />
            <Skeleton style={{ width: '90%', height: 11 }} />
          </span>
          <span className="grid justify-items-end gap-[7px]">
            <Skeleton style={{ width: 56, height: 14 }} />
            <Skeleton style={{ width: 72, height: 11 }} />
          </span>
        </div>
      ))}
    </>
  )
}

export function JournalPage() {
  const { t } = useTranslation('journal')
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(60_000)
  const narrow = useNarrow()
  const [params, setParams] = useSearchParams()
  const f = useMemo(() => readFilters(params), [params])
  const entryId = params.get('entry')
  const filtered = isFiltered(f)

  const list = useJournal(f, env, now)
  const entries = useMemo(() => list.data?.pages.flatMap((p) => p.data) ?? [], [list.data])
  const inList = entryId ? entries.find((e) => e.id === entryId) : undefined
  const fetched = useJournalEntry(entryId && !inList ? entryId : null, env)
  // With nothing chosen on a wide window, the newest shows without being written to the URL.
  const selected: JournalEntry | null | undefined = entryId ? (inList ?? fetched.data) : narrow ? null : (entries[0] ?? null)
  const notFound = Boolean(entryId) && !inList && fetched.isSuccess && fetched.data === null

  const hrefFor = useCallback((next: JournalFilters, entry?: string) => {
    const q = filterQuery(next)
    if (entry) q.set('entry', entry)
    const s = q.toString()
    return `/journal${s ? `?${s}` : ''}`
  }, [])

  const setFilters = (next: JournalFilters) => setParams(filterQuery(next), { replace: true })

  const docRef = useRef<HTMLElement>(null)
  const entryPane = useRef<HTMLDivElement>(null)
  const listPane = useRef<HTMLElement>(null)
  const focusOnOpen = useRef(false)
  const open = (id: string) => {
    focusOnOpen.current = true
    const q = filterQuery(f)
    q.set('entry', id)
    setParams(q, { replace: !narrow })
  }
  useEffect(() => {
    if (!focusOnOpen.current || !selected) return
    focusOnOpen.current = false
    if (entryPane.current) entryPane.current.scrollTop = 0
    docRef.current?.focus({ preventScroll: true })
  }, [selected])

  // Infinite scroll: the spinner row at the end asks for the next page as it nears the viewport.
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasNextPage || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      (es) => {
        if (es.some((x) => x.isIntersecting) && !isFetchingNextPage) void fetchNextPage()
      },
      { root: listPane.current, rootMargin: '160px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, entries.length])

  const groups = useMemo(() => {
    const out: Array<{ end: number; items: JournalEntry[] }> = []
    for (const e of entries) {
      const end = weekEnd(isoMs(closedAt(e)))
      const last = out[out.length - 1]
      if (last && last.end === end) last.items.push(e)
      else out.push({ end, items: [e] })
    }
    return out
  }, [entries])

  const loading = list.isPending
  const failed = list.isError && entries.length === 0
  const empty = !loading && !failed && entries.length === 0 && !filtered
  const noMatch = !loading && !failed && entries.length === 0 && filtered
  const showDoc = !narrow || Boolean(entryId)
  const view = narrow && entryId ? 'entry' : 'list'
  const listHref = hrefFor(f)

  const bar = <JournalFilterBar f={f} onChange={setFilters} now={now} lang={lang} disabled={loading || failed || empty} />

  if (failed || empty) {
    return (
      <div className="jr" data-testid="journal-page" data-state={failed ? 'error' : 'empty'}>
        {bar}
        <div className="jr-split is-single">
          {failed ? (
            <State
              kind="error"
              icon={CloudOff}
              title={t('err.title')}
              body={t('err.body')}
              action={
                <Button variant="secondary" onClick={() => void list.refetch()}>
                  <RefreshCw aria-hidden="true" strokeWidth={1.75} />
                  {t('retry')}
                </Button>
              }
            />
          ) : (
            <State
              kind="empty"
              icon={NotebookPen}
              title={t('empty.title')}
              body={t('empty.body')}
              action={
                <Button asChild variant="secondary">
                  <Link to="/portfolio/positions">
                    <Layers aria-hidden="true" strokeWidth={1.75} />
                    {t('empty.cta')}
                  </Link>
                </Button>
              }
            />
          )}
        </div>
      </div>
    )
  }

  let doc: ReactNode
  if (loading || (entryId && !inList && fetched.isPending)) {
    doc = (
      <div className="jr-doc" aria-hidden="true">
        <Skeleton style={{ width: 260, height: 30 }} />
        <Skeleton style={{ width: 340, height: 13 }} />
        <Skeleton style={{ width: '100%', height: 96, borderRadius: 12 }} />
        <Skeleton style={{ width: '100%', height: 120, borderRadius: 12 }} />
      </div>
    )
  } else if (notFound) {
    doc = (
      <State
        kind="notfound"
        icon={FileText}
        title={t('nf.title', { env: t(`env.${env}`) })}
        body={t('nf.body')}
        action={
          <Button variant="secondary" onClick={() => setFilters(f)}>
            {t('nf.cta')}
          </Button>
        }
      />
    )
  } else if (fetched.isError && entryId && !inList) {
    doc = (
      <State
        kind="error"
        icon={CloudOff}
        title={t('err.title')}
        body={t('err.body')}
        action={
          <Button variant="secondary" onClick={() => void fetched.refetch()}>
            <RefreshCw aria-hidden="true" strokeWidth={1.75} />
            {t('retry')}
          </Button>
        }
      />
    )
  } else if (selected) {
    doc = (
      <JournalEntryView
        key={selected.id}
        ref={docRef}
        e={selected}
        lang={lang}
        backHref={narrow ? listHref : undefined}
        tagHref={(tag) => hrefFor({ ...f, tag })}
        agentHref={(agent) => hrefFor({ ...f, agent })}
      />
    )
  } else {
    doc = null
  }

  const first = entries[entries.length - 1]

  return (
    <div className="jr" data-testid="journal-page" data-view={view} data-state={loading ? 'loading' : 'ready'}>
      {bar}
      <div className="jr-split">
        <section className="jr-list" ref={listPane} aria-label={t('list')} aria-busy={loading || list.isFetching || undefined} data-testid="journal-list">
          {loading ? (
            <SkeletonRows />
          ) : noMatch ? (
            <State
              kind="none"
              icon={SearchX}
              title={t('none.title')}
              body={t('none.body')}
              action={
                <Button variant="secondary" onClick={() => setFilters({ ...f, inst: '', agent: 'all', tag: 'all', outcome: 'all', range: 'all', from: '', to: '' })}>
                  {t('f.clear')}
                </Button>
              }
            />
          ) : (
            <>
              {groups.map((g) => {
                const current = g.end > now
                const label = current ? t('thisWeek') : t('weekTo', { date: formatDate(g.end, lang) })
                return (
                  <section key={g.end} className="jr-group" aria-label={label}>
                    <h2 className="jr-group-head">
                      <span>{label}</span>
                      {current && <span className="jr-group-note">{t('nextReview')}</span>}
                    </h2>
                    {g.items.map((e) => (
                      <JournalRow key={e.id} e={e} href={hrefFor(f, e.id)} selected={selected?.id === e.id} lang={lang} onOpen={() => open(e.id)} />
                    ))}
                  </section>
                )
              })}
              {hasNextPage ? (
                <div className="jr-more" ref={sentinel} role="status" data-testid="journal-more">
                  <Spinner />
                  <span>{t('loadingMore')}</span>
                </div>
              ) : (
                !filtered && first && <p className="jr-end">{t('end', { date: formatDate(closedAt(first), lang, true) })}</p>
              )}
            </>
          )}
        </section>
        {showDoc && (
          <div className="jr-entry" ref={entryPane} data-testid="journal-entry-pane">
            {doc}
          </div>
        )}
      </div>
    </div>
  )
}
