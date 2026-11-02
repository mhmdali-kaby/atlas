import { Tooltip } from 'radix-ui'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from './tradingEnv'

/**
 * Design §9 Badge: the environment capsule in the toolbar ("Demo", teal). `variant="line"` is the
 * compact toolbar's form of it (design v19 ATL-224 `.toolbar-env`): a small line under the page
 * title, shown only below 1000 px of toolbar width, where the capsule steps aside (shell.css).
 */
export function EnvBadge({ variant = 'badge' }: { variant?: 'badge' | 'line' }) {
  const { t } = useTranslation('shell')
  const tradingEnv = useTradingEnv()
  return (
    <Tooltip.Provider delayDuration={450}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          {variant === 'line' ? (
            <span
              tabIndex={0}
              data-env={tradingEnv}
              data-testid="toolbar-env"
              aria-description={tradingEnv === 'demo' ? t('env.demoTip') : undefined}
              className="shell-toolbar-env"
            >
              <span aria-hidden="true" className="dot" />
              <span>{t(`env.${tradingEnv}`)}</span>
            </span>
          ) : (
            <span
              tabIndex={0}
              data-env={tradingEnv}
              aria-description={tradingEnv === 'demo' ? t('env.demoTip') : undefined}
              className="shell-env-badge inline-flex h-[26px] shrink-0 items-center gap-[5px] rounded-full bg-demo-wash px-2.5 text-footnote font-semibold whitespace-nowrap text-demo-ink"
            >
              <span aria-hidden="true" className="size-1.5 rounded-full bg-demo" />
              {t(`env.${tradingEnv}`)}
            </span>
          )}
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
