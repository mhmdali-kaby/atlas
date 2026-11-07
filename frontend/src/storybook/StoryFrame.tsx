import { useLayoutEffect, type ReactNode } from 'react'
import i18n from '../i18n'
import { applyResolvedTheme, resolveTheme, watchSystemTheme, type ThemeChoice } from '../theme/theme'

export type StoryDirection = 'ltr' | 'rtl'

/**
 * Storybook's root for every story: applies the toolbar's theme to <html> (the
 * same `dark` class the app uses) and its direction to <html> and the story root.
 * RTL means Arabic, so lang="ar" switches the Arabic font stack and line heights.
 */
export function StoryFrame({
  theme,
  direction,
  children,
}: {
  theme: ThemeChoice
  direction: StoryDirection
  children: ReactNode
}) {
  const lang = direction === 'rtl' ? 'ar' : 'en'

  useLayoutEffect(() => {
    applyResolvedTheme(resolveTheme(theme))
    if (theme !== 'system') return
    return watchSystemTheme((prefersDark) => applyResolvedTheme(prefersDark ? 'dark' : 'light'))
  }, [theme])

  useLayoutEffect(() => {
    if (i18n.language !== lang) void i18n.changeLanguage(lang)
    document.documentElement.lang = lang
    document.documentElement.dir = direction
  }, [lang, direction])

  return (
    <div data-story-root="" dir={direction} lang={lang} className="min-h-dvh bg-background text-body text-label">
      {children}
    </div>
  )
}
