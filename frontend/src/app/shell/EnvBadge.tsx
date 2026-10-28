import { Tooltip } from 'radix-ui'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from './tradingEnv'

/** Design §9 Badge: the environment capsule in the toolbar ("Demo", teal). */
export function EnvBadge() {
  const { t } = useTranslation('shell')
  const tradingEnv = useTradingEnv()
  return (
    <Tooltip.Provider delayDuration={450}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span
            tabIndex={0}
            data-env={tradingEnv}
            aria-description={tradingEnv === 'demo' ? t('env.demoTip') : undefined}
            className="inline-flex h-[26px] shrink-0 items-center gap-[5px] rounded-full bg-demo-wash px-2.5 text-footnote font-semibold whitespace-nowrap text-demo-ink"
          >
            <span aria-hidden="true" className="size-1.5 rounded-full bg-demo" />
            {t(`env.${tradingEnv}`)}
          </span>
        </Tooltip.Trigger>
        {tradingEnv === 'demo' && (
          <Tooltip.Portal>
            <Tooltip.Content
              side="bottom"
              sideOffset={6}
              className="z-70 max-w-[260px] rounded-card px-3 py-1.5 text-footnote text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
            >
              {t('env.demoTip')}
            </Tooltip.Content>
          </Tooltip.Portal>
        )}
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
