/**
 * Theme choice: follow the OS ("system"), or force light or dark.
 *
 * The choice lives in localStorage under `atlas.theme`; a missing key means
 * "system". The inline script in index.html applies the same rule before the
 * first paint, so this module and that script must agree (see theme.test.ts).
 */
export type ThemeChoice = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'atlas.theme'
export const THEME_CHOICES: readonly ThemeChoice[] = ['system', 'light', 'dark']
const DARK_QUERY = '(prefers-color-scheme: dark)'

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === 'system' || value === 'light' || value === 'dark'
}

export function readStoredChoice(storage: Pick<Storage, 'getItem'> | undefined = safeStorage()): ThemeChoice {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function writeStoredChoice(choice: ThemeChoice, storage: Storage | undefined = safeStorage()): void {
  try {
    if (choice === 'system') storage?.removeItem(THEME_STORAGE_KEY)
    else storage?.setItem(THEME_STORAGE_KEY, choice)
  } catch {
    // Private mode or storage disabled: the choice lasts for this page only.
  }
}

export function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(DARK_QUERY).matches
    : false
}

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean = systemPrefersDark()): ResolvedTheme {
  if (choice === 'system') return prefersDark ? 'dark' : 'light'
  return choice
}

/** Puts the resolved theme on <html> (or any root): the `dark` class drives every token. */
export function applyResolvedTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  root.classList.toggle('dark', resolved === 'dark')
}

/** Calls `onChange` when the OS switches between light and dark. Returns the unsubscribe. */
export function watchSystemTheme(onChange: (prefersDark: boolean) => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const query = window.matchMedia(DARK_QUERY)
  const listener = (event: MediaQueryListEvent) => onChange(event.matches)
  query.addEventListener('change', listener)
  return () => query.removeEventListener('change', listener)
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window !== 'undefined' ? window.localStorage : undefined
  } catch {
    return undefined
  }
}
