import { Info } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from './tradingEnv'

/**
 * Brief §1.10: the permanent footer on every screen, signed in or not.
 * "Automated trading can lose money. Demo/Live: Demo"
 */
export function RiskFooter() {
  const { t } = useTranslation('shell')
  const tradingEnv = useTradingEnv()
  return (
    <footer
      role="contentinfo"
      data-env={tradingEnv}
      className="shell-footer flex items-center justify-center gap-1.5 text-center text-caption text-label-secondary vibrancy-bar"
    >
      <Info aria-hidden="true" className="size-[13px] shrink-0" strokeWidth={1.5} />
      <span>
        {t('footer.risk')} {t('footer.env')}{' '}
        <b className={tradingEnv === 'live' ? 'font-semibold text-loss-ink' : 'font-semibold text-demo-ink'}>
          {t(`env.${tradingEnv}`)}
        </b>
      </span>
    </footer>
  )
}
