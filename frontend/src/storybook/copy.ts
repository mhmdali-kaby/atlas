import { useTranslation } from 'react-i18next'

/** Story-only sample copy in both languages, so RTL stories read in Arabic. */
export function useCopy(): (en: string, ar: string) => string {
  const { i18n } = useTranslation()
  return (en, ar) => (i18n.language === 'ar' ? ar : en)
}
