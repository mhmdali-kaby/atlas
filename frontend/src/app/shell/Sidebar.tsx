import type { KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { AppIcon } from '../../ui'
import { NAV_SECTIONS, type NavEntry } from '../feature'

/**
 * Design: Shell. A floating glass source list with the "Portfolio" and "Settings"
 * sections, built from the features' nav entries. Tab reaches each item; ↑/↓ (and
 * Home/End) move between them.
 */
export function Sidebar({ entries, active, onNavigate }: { entries: readonly NavEntry[]; active?: NavEntry; onNavigate?: () => void }) {
  const { t } = useTranslation(['shell', 'common'])

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLAnchorElement>('a[data-nav-item]'))
    if (items.length === 0) return
    const current = items.indexOf(document.activeElement as HTMLAnchorElement)
    let next = 0
    if (event.key === 'End') next = items.length - 1
    else if (event.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length
    else if (event.key === 'ArrowUp') next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length
    event.preventDefault()
    items[next]?.focus()
  }

  return (
    <aside className="shell-sidebar vibrancy-sidebar" aria-label={t('nav.label')}>
      <div className="flex h-[calc(var(--toolbar-height)-8px)] shrink-0 items-center gap-2.5 px-3.5">
        <AppIcon size={26} />
        <span className="text-headline font-semibold">{t('common:appName')}</span>
      </div>
      <nav className="grid flex-1 content-start gap-[18px] overflow-auto px-2.5 pt-1.5 pb-3" onKeyDown={onKeyDown}>
        {NAV_SECTIONS.map((section) => {
          const items = entries.filter((e) => e.section === section)
          if (items.length === 0) return null
          const headingId = `nav-heading-${section}`
          return (
            <div key={section} role="group" aria-labelledby={headingId} className="grid gap-0.5">
              <h2 id={headingId} className="px-2.5 pb-1 text-footnote font-semibold text-label-secondary">
                {t(`nav.${section}`)}
              </h2>
              {items.map((entry) => {
                const Icon = entry.icon
                const current = active?.path === entry.path
                return (
                  <Link
                    key={entry.path}
                    to={entry.path}
                    data-nav-item=""
                    aria-current={current ? 'page' : undefined}
                    onClick={onNavigate}
                    className={
                      'flex h-[34px] items-center gap-2.5 rounded-[9px] px-2.5 text-subheadline text-label no-underline hover:bg-fill-4 ' +
                      (current ? 'bg-selection font-semibold hover:bg-selection' : '')
                    }
                  >
                    {Icon && <Icon aria-hidden="true" strokeWidth={1.5} className="size-[18px] shrink-0 text-accent" />}
                    <span className="truncate">{t(entry.labelKey)}</span>
                  </Link>
                )
              })}
            </div>
          )
        })}
      </nav>
    </aside>
  )
}
