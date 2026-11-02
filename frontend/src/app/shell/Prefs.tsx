import { Check, Ellipsis, Globe, LogOut, Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { setLanguage } from '../../i18n'
import { isThemeChoice, THEME_CHOICES, type ThemeChoice } from '../../theme/theme'
import { useTheme } from '../../theme/useTheme'
import { cx } from '../../ui/cx'
import { useDir } from '../../ui/useDir'
import { toolbarButton, toolbarIconButton } from './toolbarClasses'

const THEME_ICON: Record<ThemeChoice, LucideIcon> = { system: Monitor, light: Sun, dark: Moon }

/** Design: the toolbar's glass capsule holding language, appearance and the account menu. */
export function ToolbarGroup({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        'inline-flex shrink-0 items-center gap-0.5 rounded-full p-0.5 vibrancy-bar shadow-[var(--glass-edge),0_0_0_0.5px_var(--hairline-color),var(--ui-shadow-1)]',
        className,
      )}
    >
      {children}
    </div>
  )
}


/** Switches English ⇄ العربية at once (dir and lang flip, no reload) and remembers it. */
export function LanguageButton() {
  const { t, i18n } = useTranslation('shell')
  const other = i18n.language === 'ar' ? 'en' : 'ar'
  return (
    <button
      type="button"
      className={toolbarButton}
      aria-label={t('switchLang')}
      lang={other}
      data-testid="language-switch"
      onClick={() => void setLanguage(other)}
    >
      <Globe aria-hidden="true" strokeWidth={1.5} />
      <span>{t('otherLang')}</span>
    </button>
  )
}

/** The appearance menu: System / Light / Dark through ATL-14's theme store. */
export function ThemeMenu() {
  const { t } = useTranslation('shell')
  const choice = useTheme((s) => s.choice)
  const Current = THEME_ICON[choice]
  return (
    <Menu
      trigger={
        <button type="button" className={toolbarIconButton} aria-label={t('theme.label')} title={t('theme.label')}>
          <Current aria-hidden="true" strokeWidth={1.5} />
        </button>
      }
    >
      <ThemeItems />
    </Menu>
  )
}

/** The appearance choices, shared by the appearance menu and the compact toolbar's More menu. */
function ThemeItems() {
  const { t } = useTranslation('shell')
  const choice = useTheme((s) => s.choice)
  const setChoice = useTheme((s) => s.setChoice)
  return (
    <DropdownMenu.Group>
      <DropdownMenu.Label className="px-2.5 pt-1.5 pb-1 text-footnote font-semibold text-label-secondary">{t('theme.label')}</DropdownMenu.Label>
      <DropdownMenu.RadioGroup
        value={choice}
        onValueChange={(value) => {
          if (isThemeChoice(value)) setChoice(value)
        }}
      >
        {THEME_CHOICES.map((value) => {
          const Icon = THEME_ICON[value]
          return (
            <DropdownMenu.RadioItem key={value} value={value} className={menuItem}>
              <Icon aria-hidden="true" strokeWidth={1.5} />
              <span>{t(`theme.${value}`)}</span>
              <DropdownMenu.ItemIndicator className="ms-auto">
                <Check aria-hidden="true" strokeWidth={2} className="text-accent group-data-highlighted:text-on-solid" />
              </DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>
          )
        })}
      </DropdownMenu.RadioGroup>
    </DropdownMenu.Group>
  )
}

/** The account menu: who is signed in, then Sign out. */
export function AccountMenu({ name, email, onSignOut }: { name: string; email: string; onSignOut: () => void }) {
  const { t } = useTranslation('shell')
  const letters = initials(name)
  return (
    <Menu
      trigger={
        // ATL-223: the accessible name must start with the visible initials (axe
        // label-content-name-mismatch), so it is built from them rather than "Account" alone.
        <button type="button" className={toolbarIconButton} aria-label={t('accountAria', { initials: letters })} title={t('account')}>
          <span
            aria-hidden="true"
            className="grid size-[26px] place-items-center rounded-full text-[11px] font-semibold tracking-[0.02em] text-on-solid avatar-fill"
          >
            {letters}
          </span>
        </button>
      }
    >
      <div className="grid gap-px px-2.5 py-2">
        <span className="text-subheadline font-semibold text-label">{name}</span>
        <span dir="ltr" className="text-start text-footnote text-label-secondary">
          {email}
        </span>
      </div>
      <Separator />
      <SignOutItem onSignOut={onSignOut} />
    </Menu>
  )
}

function SignOutItem({ onSignOut }: { onSignOut: () => void }) {
  const { t } = useTranslation('shell')
  return (
    <DropdownMenu.Item className={menuItem} onSelect={onSignOut} data-testid="sign-out">
      <LogOut aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
      <span>{t('signOut')}</span>
    </DropdownMenu.Item>
  )
}

function Separator() {
  return <DropdownMenu.Separator className="mx-2.5 my-[5px] h-[0.5px] bg-separator" />
}

/** A page's own toolbar control (portalled into the toolbar's actions slot) as More lists it. */
type PageAction = { el: HTMLElement; label: string; icon: string; pressed: boolean | null }

/** Selector for the page actions More mirrors: each carries `data-menu-label` (its name in More). */
export const PAGE_ACTIONS_SELECTOR = '[data-slot="toolbar-actions"] [data-menu-label]'

function readPageActions(): PageAction[] {
  return Array.from(document.querySelectorAll<HTMLElement>(PAGE_ACTIONS_SELECTOR)).map((el) => {
    const pressed = el.getAttribute('aria-pressed')
    return {
      el,
      label: el.dataset.menuLabel ?? '',
      icon: el.querySelector('svg')?.outerHTML ?? '',
      pressed: pressed === null ? null : pressed === 'true',
    }
  })
}

/**
 * More (⋯), design v19 ATL-224: the compact toolbar (narrower than 1000 px of its own width,
 * shell.css) shows it in the bell's glass group, and it holds, in order, this page's actions
 * (with their toolbar icon; a toggle shows a check while on), language, appearance, then the
 * account. Each item runs exactly what its full-toolbar control runs: page actions are clicked
 * through, so the toolbar control and the menu can never disagree.
 */
export function MoreMenu({
  pageTitle,
  user,
  onSignOut,
}: {
  pageTitle: string
  user: { name: string; email: string } | undefined
  onSignOut: () => void
}) {
  const { t, i18n } = useTranslation('shell')
  const other = i18n.language === 'ar' ? 'en' : 'ar'
  const [actions, setActions] = useState<PageAction[]>([])
  return (
    <Menu
      className="shell-menu-more"
      onOpenChange={(open) => {
        if (open) setActions(readPageActions())
      }}
      trigger={
        <button type="button" className={cx(toolbarIconButton, 'shell-tb-more')} aria-label={t('more')} title={t('more')} data-testid="toolbar-more">
          <Ellipsis aria-hidden="true" strokeWidth={1.5} />
        </button>
      }
    >
      {actions.length > 0 && (
        <>
          <DropdownMenu.Group aria-label={pageTitle} data-testid="more-page-actions">
            {actions.map((a) => {
              const icon = <span aria-hidden="true" className="more-icon" dangerouslySetInnerHTML={{ __html: a.icon }} />
              const check = <Check aria-hidden="true" strokeWidth={2} className="ms-auto text-accent group-data-highlighted:text-on-solid" />
              return a.pressed === null ? (
                <DropdownMenu.Item key={a.label} className={menuItem} onSelect={() => a.el.click()}>
                  {icon}
                  <span>{a.label}</span>
                </DropdownMenu.Item>
              ) : (
                <DropdownMenu.CheckboxItem key={a.label} className={menuItem} checked={a.pressed} onSelect={() => a.el.click()}>
                  {icon}
                  <span>{a.label}</span>
                  {a.pressed && check}
                </DropdownMenu.CheckboxItem>
              )
            })}
          </DropdownMenu.Group>
          <Separator />
        </>
      )}
      <DropdownMenu.Item className={menuItem} onSelect={() => void setLanguage(other)} data-testid="more-language">
        <Globe aria-hidden="true" strokeWidth={1.5} />
        <span lang={other}>{t('otherLang')}</span>
        <span className="sr-only">{t('switchLangSr')}</span>
      </DropdownMenu.Item>
      <Separator />
      <ThemeItems />
      {user && (
        <>
          <Separator />
          <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2.5 px-2.5 pt-2 pb-1.5">
            <span
              aria-hidden="true"
              className="grid size-[34px] place-items-center rounded-full text-[12px] font-semibold tracking-[0.02em] text-on-solid avatar-fill"
            >
              {initials(user.name)}
            </span>
            <span className="grid min-w-0 gap-px">
              <span className="truncate text-subheadline font-semibold text-label">{user.name}</span>
              <span dir="ltr" className="truncate text-start text-footnote text-label-secondary">
                {user.email}
              </span>
            </span>
          </div>
          <SignOutItem onSignOut={onSignOut} />
        </>
      )}
    </Menu>
  )
}

const menuItem =
  'group flex min-h-[30px] w-full cursor-default items-center gap-2.5 rounded-[7px] px-2.5 py-1 text-start text-subheadline text-label outline-none select-none data-highlighted:bg-accent-solid data-highlighted:text-on-solid [&_svg]:size-4 [&_svg]:shrink-0 [&>svg]:text-label-secondary data-highlighted:[&>svg]:text-on-solid'

/** Design §9 Menu: a material popover under its trigger, aligned to the inline end. */
function Menu({
  trigger,
  children,
  className,
  onOpenChange,
}: {
  trigger: ReactNode
  children: ReactNode
  className?: string
  onOpenChange?: (open: boolean) => void
}) {
  const dir = useDir()
  return (
    <DropdownMenu.Root dir={dir} modal={false} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className={cx(
            'shell-popover z-60 min-w-[220px] rounded-card p-[5px] vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]',
            className,
          )}
        >
          {children}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  const letters = parts.length > 1 ? [parts[0], parts[parts.length - 1]] : parts
  return letters.map((p) => Array.from(p ?? '')[0] ?? '').join('').toUpperCase()
}
