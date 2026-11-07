import { Tooltip } from 'radix-ui'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from './tradingEnv'

/**
 * Live readiness §3 (ATL-239, ATL-240): in live the capsule is solid orange with black text, the
 * compact line is live ink with an orange dot, and the tooltip says real money moves. The word
 * (Live) is always there, so colour is never the only signal.
 *
 * Design §9 Badge: the environment capsule in the toolbar ("Demo", teal). `variant="line"` is the
 * compact toolbar's form of it (design v19 ATL-224 `.toolbar-env`): a small line under the page
 * title, shown only below 1000 px of toolbar width, where the capsule steps aside (shell.css).
 */
export function EnvBadge({ variant = 'badge' }: { variant?: 'badge' | 'line' }) {
  const { t } = useTranslation('shell')
  const tradingEnv = useTradingEnv()
  const tip = t(tradingEnv === 'live' ? 'env.liveTip' : 'env.demoTip')
  return (
    <Tooltip.Provider delayDuration={450}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          {variant === 'line' ? (
            <span
              tabIndex={0}
              data-env={tradingEnv}
              data-testid="toolbar-env"
              aria-description={tip}
              className={'shell-toolbar-env' + (tradingEnv === 'live' ? ' is-live' : '')}
            >
              <span aria-hidden="true" className="dot" />
              <span>{t(`env.${tradingEnv}`)}</span>
            </span>
          ) : (
            <span
              tabIndex={0}
              data-env={tradingEnv}
              data-testid="env-badge"
              aria-description={tip}
              className={
                'shell-env-badge inline-flex h-[26px] shrink-0 items-center gap-[5px] rounded-full px-2.5 text-footnote font-semibold whitespace-nowrap ' +
                (tradingEnv === 'live' ? 'bg-live text-live-on' : 'bg-demo-wash text-demo-ink')
              }
            >
              <span aria-hidden="true" className={'size-1.5 rounded-full ' + (tradingEnv === 'live' ? 'bg-live-on' : 'bg-demo')} />
              {t(`env.${tradingEnv}`)}
            </span>
          )}
        </Tooltip.Trigger>
        <Tooltip.Portal>
            <Tooltip.Content
              side="bottom"
              sideOffset={6}
              className="z-70 max-w-[260px] rounded-card px-3 py-1.5 text-footnote text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
            >
              {tip}
            </Tooltip.Content>
          </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
