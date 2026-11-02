import type { ReactNode } from 'react'
import { create } from 'zustand'

export type ToastTone = 'success' | 'warning' | 'error' | 'info'

export interface ToastItem {
  id: number
  /** The action's own word: "Saved", "Copied", "Signed out". */
  title: ReactNode
  body?: ReactNode
  tone: ToastTone
  /**
   * The rest are for notification toasts (ATL-217, design v18 §5.5), which carry their event's
   * tile, a meta line, a live line, an action and a severity-based duration.
   */
  /** The tile's glyph, replacing the tone's icon. */
  icon?: ReactNode
  /** Extra classes on the tile (its colour). */
  tileClass?: string
  /** "now", on the title's baseline. */
  time?: string
  /** A Caption line under the body. */
  meta?: ReactNode
  /** A live line under the meta (a waiting trade's countdown). */
  extra?: ReactNode
  action?: { label: string; onSelect: () => void }
  /** Milliseconds before it closes itself; `null` stays until closed. Default TOAST_DURATION_MS. */
  durationMs?: number | null
  /** Urgent: a red ring, an "Urgent" mark beside the action, an assertive announcement. */
  urgent?: string
  /** A caller's key (a notification id), so it can be dismissed from elsewhere. */
  tag?: string
}

type ToastInput = Omit<ToastItem, 'id' | 'tone'> & { tone?: ToastTone }

interface ToastState {
  toasts: ToastItem[]
  push: (toast: ToastInput) => number
  dismiss: (id: number) => void
  dismissTag: (tag: string) => void
}

let nextId = 1

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (toast) => {
    const id = nextId++
    set((state) => ({ toasts: [...state.toasts, { tone: 'success', ...toast, id }] }))
    return id
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  dismissTag: (tag) => set((state) => ({ toasts: state.toasts.filter((t) => t.tag !== tag) })),
}))

/** Show a toast from anywhere: `toast({ title: t('saved') })`. */
export function toast(input: ToastInput): number {
  return useToasts.getState().push(input)
}

/** The "N more in Notifications" pill asks the bell to open its list through this window event. */
export const OPEN_NOTIFICATIONS_EVENT = 'atlas:open-notifications'
