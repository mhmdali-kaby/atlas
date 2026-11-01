import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { Dialog } from 'radix-ui'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { cx } from './cx'
import { FADE, SPRING } from './motion'

export type SheetTone = 'default' | 'destructive' | 'warning' | 'muted'

const TONE: Record<SheetTone, string> = {
  default: 'bg-fill-3 text-label',
  destructive: 'bg-loss-wash text-loss-ink',
  warning: 'bg-warning-wash text-warning-ink',
  muted: 'bg-fill-3 text-label-secondary',
}

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
  /** Tints the icon tile: red for destructive, orange for warning, grey for a quiet (muted) state. */
  tone?: SheetTone
  children?: ReactNode
  /** Buttons, at the inline end. Put the destructive one last. */
  actions?: ReactNode
  className?: string
  /** Called when focus would return to the trigger on close; preventDefault to send it elsewhere. */
  onCloseAutoFocus?: (event: Event) => void
  /** Called when the sheet would focus its first control on open; preventDefault to focus another. */
  onOpenAutoFocus?: (event: Event) => void
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
  onCloseAutoFocus,
  onOpenAutoFocus,
}: SheetProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const isOpen = open ?? uncontrolledOpen
  const [surface, setSurface] = useState<HTMLDivElement | null>(null)
  const clipped = useSheetClip(surface)
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
              <Dialog.Content
                asChild
                forceMount
                onCloseAutoFocus={onCloseAutoFocus}
                onOpenAutoFocus={onOpenAutoFocus}
                {...(description ? {} : { 'aria-describedby': undefined })}
              >
                <motion.div
                  ref={setSurface}
                  data-sheet=""
                  data-clipped={clipped || undefined}
                  className={cx(
                    'fixed inset-x-0 top-14 z-81 mx-auto flex max-h-[calc(100dvh-6rem)] w-[min(540px,calc(100vw-32px))] flex-col overflow-auto scroll-pb-24',
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
                          TONE[tone],
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
                  {actions && (
                    <div
                      data-sheet-actions=""
                      className={cx(
                        'sticky bottom-0 z-2 flex flex-wrap items-center justify-end gap-2 px-7 pt-4 pb-6 [background:var(--bg-elevated)]',
                        'transition-shadow duration-[var(--dur-fast)] ease-[var(--ease-out)]',
                        clipped && 'shadow-[0_-0.5px_0_var(--separator)]',
                      )}
                    >
                      {actions}
                    </div>
                  )}
                </motion.div>
              </Dialog.Content>
            </Dialog.Portal>
          )}
        </AnimatePresence>
      </MotionConfig>
    </Dialog.Root>
  )
}

/**
 * Design §12.2: a sheet's actions stay pinned to its bottom; while content is still below
 * them they carry a hairline. Re-checked on scroll, resize and content changes.
 */
function useSheetClip(el: HTMLElement | null): boolean {
  const [clipped, setClipped] = useState(false)
  const frame = useRef(0)
  const check = useCallback(() => {
    if (!el) return
    setClipped(el.scrollHeight - el.scrollTop - el.clientHeight > 1)
  }, [el])
  useEffect(() => {
    if (!el) return undefined
    const schedule = () => {
      cancelAnimationFrame(frame.current)
      frame.current = requestAnimationFrame(check)
    }
    schedule()
    el.addEventListener('scroll', check, { passive: true })
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule)
    resize?.observe(el)
    const mutation = typeof MutationObserver === 'undefined' ? null : new MutationObserver(schedule)
    mutation?.observe(el, { childList: true, subtree: true, characterData: true })
    return () => {
      cancelAnimationFrame(frame.current)
      el.removeEventListener('scroll', check)
      resize?.disconnect()
      mutation?.disconnect()
    }
  }, [el, check])
  return clipped
}

/** Closes the enclosing Sheet; wrap a Button in it. */
export function SheetClose({ children }: { children: ReactNode }) {
  return <Dialog.Close asChild>{children}</Dialog.Close>
}
