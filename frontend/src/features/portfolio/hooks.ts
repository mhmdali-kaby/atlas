import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'

export function useLang(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/** Re-renders every `ms` so "N minutes ago" keeps counting between polls. */
export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(id)
  }, [ms])
  return now
}
