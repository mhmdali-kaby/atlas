import { Bell, CircleAlert, CircleCheck, Info, OctagonAlert, TriangleAlert, X } from 'lucide-react'
import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { Toast as RadixToast } from 'radix-ui'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from './cx'
import { SPRING } from './motion'
import { OPEN_NOTIFICATIONS_EVENT, useToasts, type ToastItem, type ToastTone } from './toastStore'
import { useDir } from './useDir'

/** Design §9: toasts dismiss themselves after 4.2s. */
export const TOAST_DURATION_MS = 4200
/** Design v18 §5.5: three show at most; the rest fold into a "N more" pill. */
export const TOAST_VISIBLE = 3

const TONE_ICON: Record<ToastTone, ReactNode> = {
  success: <CircleCheck strokeWidth={1.5} />,
  warning: <TriangleAlert strokeWidth={1.5} />,
  error: <CircleAlert strokeWidth={1.5} />,
  info: <Info strokeWidth={1.5} />,
}

const TONE_TILE: Record<ToastTone, string> = {
  success: 'bg-profit-wash text-profit-ink',
  warning: 'bg-warning-wash text-warning-ink',
  error: 'bg-loss-wash text-loss-ink',
  info: 'bg-accent-wash text-accent-ink',
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const { t } = useTranslation('ui')
  const dir = useDir()
  const fromX = dir === 'rtl' ? -24 : 24
  const rich = item.action !== undefined || item.icon !== undefined
  const duration = item.durationMs === null ? Infinity : (item.durationMs ?? TOAST_DURATION_MS)

  return (
    <RadixToast.Root
      asChild
      forceMount
      duration={duration}
      type={item.tone === 'error' || item.urgent ? 'foreground' : 'background'}
      onOpenChange={(open) => {
        if (!open) onDismiss()
      }}
    >
      <motion.li
        layout
        data-toast={item.tone}
        data-urgent={item.urgent ? true : undefined}
        initial={{ opacity: 0, x: fromX }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, scale: 0.96 }}
        className={cx(
          'group/toast relative flex items-start gap-3 rounded-panel px-3.5 py-3 text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)] focus-visible:shadow-[var(--ui-focus-ring),var(--ui-shadow-popover)]',
          item.urgent && 'shadow-[var(--ui-shadow-popover),0_0_0_1px_color-mix(in_srgb,var(--loss)_42%,transparent)]',
        )}
      >
        <span
          aria-hidden="true"
          className={cx(
            'grid shrink-0 place-items-center',
            rich ? 'size-8 rounded-[9px] [&_svg]:size-[17px]' : 'size-7 rounded-[8px] [&_svg]:size-4',
            item.tileClass ?? TONE_TILE[item.tone],
          )}
        >
          {item.icon ?? TONE_ICON[item.tone]}
        </span>
        <div className={cx('grid min-w-0 flex-1 gap-0.5', rich && 'pe-[18px]')}>
          <div className="flex items-baseline justify-between gap-2.5">
            <RadixToast.Title className="min-w-0 text-subheadline font-semibold">{item.title}</RadixToast.Title>
            {item.time && <span className="shrink-0 text-footnote text-label-secondary">{item.time}</span>}
          </div>
          {item.body && (
            <RadixToast.Description className={cx('text-footnote text-label-secondary', rich && 'line-clamp-3')}>
              {item.body}
            </RadixToast.Description>
          )}
          {item.meta && <p className="text-caption text-label-secondary">{item.meta}</p>}
          {item.extra}
          {item.action && (
            <div className="mt-2 flex items-center gap-2.5">
              <RadixToast.Action asChild altText={item.action.label}>
                <button
                  type="button"
                  className="inline-flex h-7 items-center rounded-[8px] bg-fill-2 px-2.5 text-footnote font-semibold text-label hover:bg-fill-1 focus-visible:shadow-focus"
                  onClick={item.action.onSelect}
                >
                  {item.action.label}
                </button>
              </RadixToast.Action>
              {item.urgent && (
                <span className="inline-flex items-center gap-1 text-caption font-semibold text-loss-ink">
                  <OctagonAlert aria-hidden="true" className="size-[13px]" strokeWidth={1.75} />
                  {item.urgent}
                </span>
              )}
            </div>
          )}
        </div>
        <RadixToast.Close
          aria-label={t('dismiss')}
          className={cx(
            'grid shrink-0 place-items-center text-label-secondary hover:text-label',
            rich
              ? 'absolute end-2 top-2 size-[22px] rounded-full bg-fill-3 opacity-0 transition-opacity group-hover/toast:opacity-100 group-focus-within/toast:opacity-100 [@media(hover:none)]:opacity-100'
              : 'size-6 rounded-[6px] hover:bg-fill-3',
            item.urgent && 'opacity-100',
          )}
        >
          <X aria-hidden="true" className={rich ? 'size-3' : 'size-3.5'} strokeWidth={rich ? 2 : 1.5} />
        </RadixToast.Close>
      </motion.li>
    </RadixToast.Root>
  )
}

/**
 * Mount once at the app root. Toasts stack at the top end like macOS
 * notifications (design §9), in a polite live region, on the 300/30 spring.
 */
export function Toaster() {
  const toasts = useToasts((s) => s.toasts)
  const dismiss = useToasts((s) => s.dismiss)
  const { t } = useTranslation('ui')
  const dir = useDir()

  return (
    <RadixToast.Provider duration={TOAST_DURATION_MS} swipeDirection={dir === 'rtl' ? 'left' : 'right'} label={t('notifications')}>
      <MotionConfig reducedMotion="user" transition={SPRING}>
        <AnimatePresence initial={false}>
          {[...toasts]
            .reverse()
            .slice(0, TOAST_VISIBLE)
            .map((item) => (
              <ToastCard key={item.id} item={item} onDismiss={() => dismiss(item.id)} />
            ))}
        </AnimatePresence>
      </MotionConfig>
      <div className="pointer-events-none fixed end-4 top-14 z-90 grid w-[368px] max-w-[calc(100vw-32px)] justify-items-end gap-2 max-[640px]:end-2 max-[640px]:max-w-[calc(100vw-16px)]">
        <RadixToast.Viewport className="pointer-events-auto m-0 grid w-full list-none gap-2 p-0 outline-none" />
        {toasts.length > TOAST_VISIBLE && (
          <button
            type="button"
            data-testid="toast-more"
            className="pointer-events-auto inline-flex h-[30px] items-center gap-1.5 rounded-full px-3 text-footnote font-semibold text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)] focus-visible:shadow-focus"
            onClick={() => window.dispatchEvent(new Event(OPEN_NOTIFICATIONS_EVENT))}
          >
            <Bell aria-hidden="true" className="size-3.5" strokeWidth={1.5} />
            {t('more', { count: toasts.length - TOAST_VISIBLE })}
          </button>
        )}
      </div>
    </RadixToast.Provider>
  )
}
