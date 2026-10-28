import { useTranslation } from 'react-i18next'

/** The current text direction, from the active language. Re-renders on change. */
export function useDir(): 'ltr' | 'rtl' {
  const { i18n } = useTranslation()
  return i18n.dir(i18n.language) === 'rtl' ? 'rtl' : 'ltr'
}
