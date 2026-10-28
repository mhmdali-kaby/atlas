import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

/*
 * One JSON file per feature namespace per language: src/i18n/locales/<lang>/<namespace>.json.
 * They are discovered by glob, so a feature adds its own files and never edits this one.
 */
const modules = import.meta.glob<{ default: Record<string, unknown> }>('./locales/*/*.json', { eager: true })

const resources: Record<string, Record<string, Record<string, unknown>>> = {}
for (const [path, mod] of Object.entries(modules)) {
  const match = /\.\/locales\/([^/]+)\/([^/]+)\.json$/.exec(path)
  if (!match) continue
  const [, lang, ns] = match as unknown as [string, string, string]
  resources[lang] ??= {}
  resources[lang][ns] = mod.default
}

export const RTL_LANGUAGES = new Set(['ar'])

export function applyDocumentDirection(lang: string): void {
  document.documentElement.lang = lang
  document.documentElement.dir = RTL_LANGUAGES.has(lang) ? 'rtl' : 'ltr'
}

void i18n.use(initReactI18next).init({
  resources,
  lng: 'en',
  fallbackLng: 'en',
  defaultNS: 'common',
  interpolation: { escapeValue: false },
})

i18n.on('languageChanged', applyDocumentDirection)
applyDocumentDirection(i18n.language)

export default i18n
