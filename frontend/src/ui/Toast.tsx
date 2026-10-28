import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react'
import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { Toast as RadixToast } from 'radix-ui'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from './cx'
import { SPRING } from './motion'
import { useToasts, type ToastItem, type ToastTone } from './toastStore'
import { useDir } from './useDir'

/** Design §9: toasts dismiss themselves after 4.2s. */
export const TOAST_DURATION_MS = 4200

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

  return (
    <RadixToast.Root
      asChild
      forceMount
      duration={TOAST_DURATION_MS}
      type={item.tone === 'error' ? 'foreground' : 'background'}
      onOpenChange={(open) => {
        if (!open) onDismiss()
      }}
    >
      <motion.li
        layout
        data-toast={item.tone}
        initial={{ opacity: 0, x: fromX }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, scale: 0.96 }}
        className="flex items-start gap-3 rounded-panel px-3.5 py-3 text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)] focus-visible:shadow-[var(--ui-focus-ring),var(--ui-shadow-popover)]"
      >
        <span
          aria-hidden="true"
          className={cx('grid size-7 shrink-0 place-items-center rounded-[8px] [&_svg]:size-4', TONE_TILE[item.tone])}
        >
          {TONE_ICON[item.tone]}
        </span>
        <div className="grid min-w-0 flex-1 gap-0.5">
          <RadixToast.Title className="text-subheadline font-semibold">{item.title}</RadixToast.Title>
          {item.body && (
            <RadixToast.Description className="text-footnote text-label-secondary">{item.body}</RadixToast.Description>
          )}
        </div>
        <RadixToast.Close
          aria-label={t('dismiss')}
          className="grid size-6 shrink-0 place-items-center rounded-[6px] text-label-secondary hover:bg-fill-3 hover:text-label"
        >
          <X aria-hidden="true" className="size-3.5" strokeWidth={1.5} />
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
          {toasts.map((item) => (
            <ToastCard key={item.id} item={item} onDismiss={() => dismiss(item.id)} />
          ))}
        </AnimatePresence>
      </MotionConfig>
      <RadixToast.Viewport className="fixed end-4 top-14 z-90 m-0 grid w-[340px] max-w-[calc(100vw-32px)] list-none gap-2 p-0 outline-none" />
    </RadixToast.Provider>
  )
}
