import { Wallet } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toolbarIconButton } from './toolbarClasses'
import { useTradingEnv, type TradingEnv } from './tradingEnv'

/**
 * Opens the owner's OKX account for the environment Atlas trades in. OKX keeps demo vs live as a
 * switch on okx.com itself (its `simulatedTrading` cookie), not in the URL, so the link picks the
 * page and the tooltip tells the owner which switch position to expect.
 */
const OKX_ACCOUNT_URL: Record<TradingEnv, string> = {
  demo: 'https://www.okx.com/balance/assets',
  live: 'https://www.okx.com/balance/overview',
}

export function OkxAccountLink() {
  const { t } = useTranslation('shell')
  const env = useTradingEnv()
  return (
    <a
      href={OKX_ACCOUNT_URL[env]}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="okx-account-link"
      className={toolbarIconButton}
      aria-label={t(`okxAccount.${env}`)}
      title={`${t(`okxAccount.${env}`)}. ${t(`okxAccount.${env}Hint`)}`}
    >
      <Wallet aria-hidden="true" strokeWidth={1.5} />
    </a>
  )
}
