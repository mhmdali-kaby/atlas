import { Compass, PanelLeft } from 'lucide-react'
import { MotionConfig } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, Outlet, useLocation } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { SIGNED_OUT_KEY, useLogout, useSession } from '../features/auth/session'
import { toast } from '../ui'
import { activeNavEntry, type NavEntry } from './feature'
import { navEntries as registeredNavEntries } from './registry'
import { EnvBadge } from './shell/EnvBadge'
import { AccountMenu, LanguageButton, ThemeMenu, ToolbarGroup } from './shell/Prefs'
import { toolbarIconButton } from './shell/toolbarClasses'
import { RiskFooter } from './shell/RiskFooter'
import { Sidebar } from './shell/Sidebar'

const NARROW_QUERY = '(max-width: 760px)'

/**
 * The macOS-style shell (design: Shell, css/shell.css): a floating glass sidebar
 * from the features' nav entries, a unified toolbar (title, Demo badge, language,
 * appearance, account) and the permanent risk footer. Rendered inside RequireAuth.
 */
export function AppShell({ nav = registeredNavEntries }: { nav?: readonly NavEntry[] }) {
  const { t } = useTranslation(['shell', 'common'])
  const location = useLocation()
  const client = useQueryClient()
  const session = useSession()
  const logout = useLogout()
  const active = activeNavEntry(nav, location.pathname)
  const title = active ? t(active.labelKey) : t('common:appName')

  const [sidebarHidden, setSidebarHidden] = useState(false)
  const [narrowOpen, setNarrowOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const contentRef = useRef<HTMLElement>(null)
  const firstRender = useRef(true)

  // A new page: back to the top, focus on <main> so the reader starts there.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false
      return
    }
    contentRef.current?.focus({ preventScroll: true })
  }, [location.pathname])

  useEffect(() => {
    document.title = active ? `${title} · ${t('common:appName')}` : t('common:appName')
  }, [active, title, t])

  function toggleSidebar() {
    if (window.matchMedia?.(NARROW_QUERY).matches) setNarrowOpen((open) => !open)
    else setSidebarHidden((hidden) => !hidden)
  }

  // The guard sees the session end and redirects; SIGNED_OUT_KEY makes that a plain
  // /login (no `next`), replacing this entry so Back cannot return to the page.
  function signOut() {
    client.setQueryData(SIGNED_OUT_KEY, true)
    // useLogout ends the local session even when the request fails, so this is true either way.
    logout.mutate()
    toast({ title: t('signedOut'), tone: 'info' })
  }

  const user = session.data

  return (
    <MotionConfig reducedMotion="user">
      <div className="shell-wash" aria-hidden="true" />
      <div className="shell-app" data-sidebar={sidebarHidden ? 'hidden' : 'shown'} data-narrow-open={narrowOpen}>
        <Sidebar entries={nav} active={active} onNavigate={() => setNarrowOpen(false)} />
        <div className="shell-main">
          <header className="shell-toolbar" data-scrolled={scrolled} aria-label={t('toolbar')}>
            <button
              type="button"
              className={toolbarIconButton}
              aria-label={t('toggleSidebar')}
              title={t('toggleSidebar')}
              aria-expanded={!sidebarHidden}
              onClick={toggleSidebar}
            >
              <PanelLeft aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
            </button>
            <h1 className="ms-1 truncate text-headline font-semibold">{title}</h1>
            <div className="flex-1" />
            {/* ⌘K palette and kill switch slot (later patches). */}
            <div data-slot="toolbar-actions" className="contents" />
            <EnvBadge />
            <ToolbarGroup>
              <LanguageButton />
              <ThemeMenu />
              {user && <AccountMenu name={user.name} email={user.email} onSignOut={signOut} />}
            </ToolbarGroup>
          </header>
          <main
            id="content"
            ref={contentRef}
            tabIndex={-1}
            className="shell-content"
            onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 4)}
          >
            <div key={location.pathname} className="shell-page">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
      <RiskFooter />
    </MotionConfig>
  )
}

/** Signed in at "/": open the first page a feature registered. */
export function HomeRedirect({ nav = registeredNavEntries }: { nav?: readonly NavEntry[] }) {
  const first = nav[0]
  return first ? <Navigate to={first.path} replace /> : <NotFound />
}

/** Design §8 empty state for a URL no feature serves (yet). */
export function NotFound() {
  const { t } = useTranslation('shell')
  return (
    <div className="grid justify-items-center gap-3 px-6 pt-24 text-center">
      <span className="grid size-14 place-items-center rounded-panel bg-fill-4 text-label-secondary">
        <Compass aria-hidden="true" strokeWidth={1.5} className="size-7" />
      </span>
      <h2 className="text-title-3 text-label">{t('notFound.title')}</h2>
      <p className="max-w-[420px] text-subheadline text-balance text-label-secondary">{t('notFound.body')}</p>
    </div>
  )
}

/** @deprecated Use RiskFooter; kept so screens built before ATL-17 still render the footer. */
export function EnvFooter(_props: { label?: string }) {
  return <RiskFooter />
}

export { RiskFooter }
