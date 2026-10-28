import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { Dialog } from 'radix-ui'
import { useState, type ReactNode } from 'react'
import { cx } from './cx'
import { FADE, SPRING } from './motion'

export interface SheetProps {
  /** Controlled open state; omit for an uncontrolled sheet opened by `trigger`. */
  open?: boolean
  defaultOpen?: boolean
  onOpenChange?: (open: boolean) => void
  /** The element that opens the sheet; focus returns to it on close. */
  trigger?: ReactNode
  title: ReactNode
  description?: ReactNode
  /** A 20px icon shown in a 44px tile above the title. */
  icon?: ReactNode
  /** A destructive sheet tints the icon tile red. */
  tone?: 'default' | 'destructive'
  children?: ReactNode
  /** Buttons, at the inline end. Put the destructive one last. */
  actions?: ReactNode
  className?: string
}

/**
 * A macOS sheet (design §9): drops from under the toolbar, 540px wide, radius
 * 20, over a scrim, on the 300/30 spring. Radix Dialog traps focus, closes on
 * Esc and on the scrim, and returns focus to the trigger.
 */
export function Sheet({
  open,
  defaultOpen = false,
  onOpenChange,
  trigger,
  title,
  description,
  icon,
  tone = 'default',
  children,
  actions,
  className,
}: SheetProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const isOpen = open ?? uncontrolledOpen
  const setOpen = (next: boolean) => {
    if (open === undefined) setUncontrolledOpen(next)
    onOpenChange?.(next)
  }

  return (
    <Dialog.Root open={isOpen} onOpenChange={setOpen}>
      {trigger && <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>}
      <MotionConfig reducedMotion="user">
        <AnimatePresence>
          {isOpen && (
            <Dialog.Portal forceMount>
              <Dialog.Overlay asChild forceMount>
                <motion.div
                  className="fixed inset-0 z-80 bg-scrim"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={FADE}
                />
              </Dialog.Overlay>
              <Dialog.Content asChild forceMount {...(description ? {} : { 'aria-describedby': undefined })}>
                <motion.div
                  data-sheet=""
                  className={cx(
                    'fixed inset-x-0 top-14 z-81 mx-auto flex max-h-[calc(100dvh-6rem)] w-[min(540px,calc(100vw-32px))] flex-col overflow-auto',
                    'sheet-surface text-label focus:outline-none',
                    className,
                  )}
                  initial={{ opacity: 0, y: -24, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -24, scale: 0.98 }}
                  transition={SPRING}
                >
                  <div className="grid gap-4 px-7 pt-7 pb-5">
                    {icon && (
                      <span
                        aria-hidden="true"
                        className={cx(
                          'grid size-11 place-items-center rounded-card [&_svg]:size-5',
                          tone === 'destructive' ? 'bg-loss-wash text-loss-ink' : 'bg-fill-3 text-label',
                        )}
                      >
                        {icon}
                      </span>
                    )}
                    <div className="grid gap-1">
                      <Dialog.Title className="text-title-3">{title}</Dialog.Title>
                      {description ? (
                        <Dialog.Description className="text-subheadline text-label-secondary">
                          {description}
                        </Dialog.Description>
                      ) : null}
                    </div>
                    {children}
                  </div>
                  {actions && <div className="flex justify-end gap-2 px-7 pt-4 pb-6">{actions}</div>}
                </motion.div>
              </Dialog.Content>
            </Dialog.Portal>
          )}
        </AnimatePresence>
      </MotionConfig>
    </Dialog.Root>
  )
}

/** Closes the enclosing Sheet; wrap a Button in it. */
export function SheetClose({ children }: { children: ReactNode }) {
  return <Dialog.Close asChild>{children}</Dialog.Close>
}
