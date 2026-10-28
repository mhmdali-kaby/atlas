import { Check, Globe, LogOut, Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import type { ReactNode } from 'react'
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
  const setChoice = useTheme((s) => s.setChoice)
  const Current = THEME_ICON[choice]
  return (
    <Menu
      trigger={
        <button type="button" className={toolbarIconButton} aria-label={t('theme.label')} title={t('theme.label')}>
          <Current aria-hidden="true" strokeWidth={1.5} />
        </button>
      }
    >
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
    </Menu>
  )
}

/** The account menu: who is signed in, then Sign out. */
export function AccountMenu({ name, email, onSignOut }: { name: string; email: string; onSignOut: () => void }) {
  const { t } = useTranslation('shell')
  return (
    <Menu
      trigger={
        <button type="button" className={toolbarIconButton} aria-label={t('account')} title={t('account')}>
          <span
            aria-hidden="true"
            className="grid size-[26px] place-items-center rounded-full text-[11px] font-semibold tracking-[0.02em] text-on-solid avatar-fill"
          >
            {initials(name)}
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
      <DropdownMenu.Separator className="mx-2.5 my-[5px] h-[0.5px] bg-separator" />
      <DropdownMenu.Item className={menuItem} onSelect={onSignOut}>
        <LogOut aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        <span>{t('signOut')}</span>
      </DropdownMenu.Item>
    </Menu>
  )
}

const menuItem =
  'group flex min-h-[30px] w-full cursor-default items-center gap-2.5 rounded-[7px] px-2.5 py-1 text-start text-subheadline text-label outline-none select-none data-highlighted:bg-accent-solid data-highlighted:text-on-solid [&_svg]:size-4 [&_svg]:shrink-0 [&>svg]:text-label-secondary data-highlighted:[&>svg]:text-on-solid'

/** Design §9 Menu: a material popover under its trigger, aligned to the inline end. */
function Menu({ trigger, children }: { trigger: ReactNode; children: ReactNode }) {
  const dir = useDir()
  return (
    <DropdownMenu.Root dir={dir} modal={false}>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="shell-popover z-60 min-w-[220px] rounded-card p-[5px] vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
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
