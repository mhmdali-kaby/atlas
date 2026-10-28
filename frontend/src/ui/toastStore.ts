import { create } from 'zustand'

export type ToastTone = 'success' | 'warning' | 'error' | 'info'

export interface ToastItem {
  id: number
  /** The action's own word: "Saved", "Copied", "Signed out". */
  title: string
  body?: string
  tone: ToastTone
}

interface ToastState {
  toasts: ToastItem[]
  push: (toast: Omit<ToastItem, 'id' | 'tone'> & { tone?: ToastTone }) => number
  dismiss: (id: number) => void
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
}))

/** Show a toast from anywhere: `toast({ title: t('saved') })`. */
export function toast(input: Parameters<ToastState['push']>[0]): number {
  return useToasts.getState().push(input)
}
