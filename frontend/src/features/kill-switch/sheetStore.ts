import { create } from 'zustand'

interface KillSheetsState {
  kill: boolean
  resume: boolean
  setKill: (open: boolean) => void
  setResume: (open: boolean) => void
}

/**
 * The toolbar's Stop sheet and a Resume confirmation, opened from elsewhere (the ⌘K palette,
 * ATL-249). Opening only shows the sheet; nothing is sent until the owner confirms in it.
 */
export const useKillSheets = create<KillSheetsState>((set) => ({
  kill: false,
  resume: false,
  setKill: (kill) => set({ kill }),
  setResume: (resume) => set({ resume }),
}))
