/**
 * The owner's language: "en" or "ar". It lives in localStorage under `atlas.lang`;
 * a missing or unknown value means English. The inline script in index.html applies
 * the same rule to <html lang dir> before the first paint (see language.test.ts).
 */
export type Language = 'en' | 'ar'

export const LANGUAGE_STORAGE_KEY = 'atlas.lang'
export const LANGUAGES: readonly Language[] = ['en', 'ar']
export const RTL_LANGUAGES: ReadonlySet<string> = new Set(['ar'])

export function isLanguage(value: unknown): value is Language {
  return value === 'en' || value === 'ar'
}

export function readStoredLanguage(storage: Pick<Storage, 'getItem'> | undefined = safeStorage()): Language {
  try {
    const value = storage?.getItem(LANGUAGE_STORAGE_KEY)
    return isLanguage(value) ? value : 'en'
  } catch {
    return 'en'
  }
}

export function writeStoredLanguage(lang: Language, storage: Storage | undefined = safeStorage()): void {
  try {
    storage?.setItem(LANGUAGE_STORAGE_KEY, lang)
  } catch {
    // Private mode or storage disabled: the choice lasts for this page only.
  }
}

/** Sets lang and dir on <html>. */
export function applyDocumentDirection(lang: string): void {
  document.documentElement.lang = lang
  document.documentElement.dir = RTL_LANGUAGES.has(lang) ? 'rtl' : 'ltr'
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window === 'undefined' ? undefined : window.localStorage
  } catch {
    return undefined
  }
}
