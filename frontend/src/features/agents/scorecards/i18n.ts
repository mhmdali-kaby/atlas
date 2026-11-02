import { useTranslation } from 'react-i18next'
import { useLang } from '../../portfolio/hooks'

/* The scorecards' i18n namespace and its hook, shared by every scorecard piece. */

export const SC_NS = 'scorecards'

export function useSc() {
  const { t } = useTranslation(SC_NS)
  return { t, lang: useLang() }
}
