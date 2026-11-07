import { create } from 'zustand'

interface PaletteState {
  open: boolean
  setOpen: (open: boolean) => void
}

/** Whether the ⌘K palette is open. The sidebar capsule and the More menu open it through here. */
export const usePaletteStore = create<PaletteState>((set, get) => ({
  open: false,
  setOpen: (open) => {
    // Remember where focus was as the palette opens, whichever door opened it; Esc returns there.
    if (open && !get().open) {
      previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
      restore = true
    }
    set({ open })
  },
}))

let previous: HTMLElement | null = null
let restore = true

/** The element to refocus on close: where focus was when it opened, unless a choice moved on. */
export const focusBeforeOpen = () => (restore ? previous : null)

/** A choice closes the palette and moves on (a page, a sheet): focus is not sent back. */
export const skipFocusReturn = () => {
  restore = false
}

export const openPalette = () => usePaletteStore.getState().setOpen(true)

/** ⌘K on a Mac, Ctrl K elsewhere (design §6). */
export const isMac = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
export const paletteKeys = () => (isMac() ? '⌘K' : 'Ctrl K')
