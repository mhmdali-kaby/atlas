import { useEffect } from 'react'
import { Info } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from './tradingEnv'

/**
 * Brief §1.10: the permanent footer on every screen, signed in or not.
 * "Automated trading can lose money. Demo/Live: Demo"
 *
 * Live readiness (ATL-240): in live the env word sits in a small solid orange capsule with a dot,
 * and the window gets the "live wire" (a 3 px orange top edge, `:root.is-live`). The footer is on
 * every screen, signed in or not, so it owns the wire.
 */
export function RiskFooter() {
  const { t } = useTranslation('shell')
  const tradingEnv = useTradingEnv()
  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('is-live', tradingEnv === 'live')
    return () => root.classList.remove('is-live')
  }, [tradingEnv])
  return (
    <footer
      role="contentinfo"
      data-env={tradingEnv}
      className="shell-footer flex items-center justify-center gap-1.5 text-center text-caption text-label-secondary vibrancy-bar"
    >
      <Info aria-hidden="true" className="size-[13px] shrink-0" strokeWidth={1.5} />
      <span>
        {t('footer.risk')} {t('footer.env')}{' '}
        <b data-testid="footer-env" className={tradingEnv === 'live' ? 'shell-footer-live' : 'font-semibold text-demo-ink'}>
          {t(`env.${tradingEnv}`)}
        </b>
      </span>
    </footer>
  )
}
