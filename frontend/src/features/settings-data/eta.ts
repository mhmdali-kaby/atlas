import { useTranslation } from 'react-i18next'
import { etaParts } from './model'
import { NS } from './parts'

/** "About 12 min left" wording for a number of seconds (design §7 rounding). */
export function useEtaText() {
  const { t } = useTranslation(NS)
  return (sec: number) => {
    const parts = etaParts(sec)
    if (parts === 'lessThanMinute') return t('eta.lessThanMinute')
    return [parts.h ? t('eta.h', { count: parts.h }) : '', parts.m ? t('eta.m', { count: parts.m }) : '']
      .filter(Boolean)
      .join(t('eta.join'))
  }
}
