import type { KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { AppIcon } from '../../ui'
import type { AreaDef, AreaId, AreaNavEntry, ShellRegistry } from '../feature'

/** ↑/↓ (and Home/End) move focus between the matching items inside `container`. */
function moveFocus(event: KeyboardEvent<HTMLElement>, selector: string): HTMLElement | undefined {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return undefined
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(selector))
  if (items.length === 0) return undefined
  const current = items.indexOf(document.activeElement as HTMLElement)
  let next = 0
  if (event.key === 'End') next = items.length - 1
  else if (event.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length
  else if (event.key === 'ArrowUp') next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length
  event.preventDefault()
  items[next]?.focus()
  return items[next]
}

interface SidebarProps {
  registry: ShellRegistry
  /** The area the current URL is in (none on a page no feature serves). */
  area?: AreaDef
  /** The sidebar link the current URL is on. */
  active?: AreaNavEntry
  /** Where each area's switcher button goes: the last page opened in it, else its first page. */
  areaHref: (area: AreaId) => string
  onNavigate?: () => void
}

/**
 * Design v4: Shell. A floating glass source list. At its top a vertical segmented control
 * switches between the areas (War Room, Portfolio, Settings); below it only the current
 * area's sections: the features' links and any sections a feature draws itself.
 * The switcher is one Tab stop (roving tabindex); ↑/↓, Home and End move inside it, and
 * Enter or Space opens the focused area. In the list, ↑/↓ move between items.
 */
export function Sidebar({ registry, area, active, areaHref, onNavigate }: SidebarProps) {
  const { t } = useTranslation(['shell', 'common'])
  const navigate = onNavigate ?? (() => {})
  const sections = area ? registry.sections.filter((s) => s.area === area.id) : []
  const tabStop = area?.id ?? registry.areas[0]?.id

  function onSwitcherKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === ' ' && (event.target as HTMLElement).matches('a[data-area]')) {
      event.preventDefault()
      ;(event.target as HTMLAnchorElement).click()
      return
    }
    moveFocus(event, 'a[data-area]')
  }

  return (
    <aside className="shell-sidebar vibrancy-sidebar" aria-label={t('nav.label')}>
      <div className="flex h-[calc(var(--toolbar-height)-8px)] shrink-0 items-center gap-2.5 px-3.5">
        <AppIcon size={26} />
        <span className="text-headline font-semibold">{t('common:appName')}</span>
      </div>
      {registry.areas.length > 0 && (
        <nav className="shell-area-switch" aria-label={t('nav.areas')} onKeyDown={onSwitcherKeyDown} data-testid="area-switch">
          {registry.areas.map((a) => {
            const Icon = a.icon
            const current = a.id === area?.id
            return (
              <Link
                key={a.id}
                to={areaHref(a.id)}
                data-area={a.id}
                className="shell-area"
                aria-current={current ? 'true' : undefined}
                tabIndex={a.id === tabStop ? 0 : -1}
                onClick={navigate}
              >
                <Icon aria-hidden="true" strokeWidth={1.5} className="shell-area-icon" />
                <span className="truncate">{t(a.labelKey)}</span>
              </Link>
            )
          })}
        </nav>
      )}
      <nav
        className="grid flex-1 content-start gap-[18px] overflow-auto px-2.5 pt-2 pb-3"
        aria-label={area ? t(area.labelKey) : t('nav.label')}
        data-testid="sidebar-sections"
        onKeyDown={(e) => moveFocus(e, '[data-nav-item]')}
      >
        {sections.map((section) => {
          if (section.kind === 'custom') {
            const { component: Section, labelKey, id } = section.section
            const headingId = `nav-heading-${id}`
            return (
              <div key={section.key} role="group" aria-labelledby={labelKey ? headingId : undefined} className="grid gap-0.5">
                {labelKey && (
                  <h2 id={headingId} className="px-2.5 pb-1 text-footnote font-semibold text-label-secondary">
                    {t(labelKey)}
                  </h2>
                )}
                <Section onNavigate={navigate} />
              </div>
            )
          }
          return (
            <div key={section.key} role="group" aria-label={area ? t(area.labelKey) : undefined} className="grid gap-0.5">
              {section.entries.map((entry) => {
                const Icon = entry.icon
                const current = active?.path === entry.path
                return (
                  <Link
                    key={entry.path}
                    to={entry.path}
                    data-nav-item=""
                    aria-current={current ? 'page' : undefined}
                    onClick={navigate}
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
