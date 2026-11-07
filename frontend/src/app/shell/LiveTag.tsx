import { useTranslation } from 'react-i18next'

/**
 * Live readiness (ATL-240): the LIVE tag beside a sheet title when the sheet acts on real money
 * (Approve a real order, Stop trading). Solid orange with a dot and the word, never colour alone.
 */
export function LiveTag() {
  const { t } = useTranslation('shell')
  return (
    <span data-testid="live-tag" className="shell-live-tag">
      {t('env.live')}
    </span>
  )
}
