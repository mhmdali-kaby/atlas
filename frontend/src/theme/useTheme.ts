import { create } from 'zustand'
import {
  applyResolvedTheme,
  readStoredChoice,
  resolveTheme,
  systemPrefersDark,
  watchSystemTheme,
  writeStoredChoice,
  type ResolvedTheme,
  type ThemeChoice,
} from './theme'

interface ThemeState {
  /** What the owner picked: system, light or dark. */
  choice: ThemeChoice
  /** What is on screen now. */
  resolved: ResolvedTheme
  setChoice: (choice: ThemeChoice) => void
  /** Re-reads the OS preference; used by the live listener. */
  syncWithSystem: (prefersDark: boolean) => void
}

const initialChoice = readStoredChoice()

export const useTheme = create<ThemeState>()((set, get) => ({
  choice: initialChoice,
  resolved: resolveTheme(initialChoice),
  setChoice: (choice) => {
    writeStoredChoice(choice)
    const resolved = resolveTheme(choice)
    applyResolvedTheme(resolved)
    set({ choice, resolved })
  },
  syncWithSystem: (prefersDark) => {
    if (get().choice !== 'system') return
    const resolved = resolveTheme('system', prefersDark)
    applyResolvedTheme(resolved)
    set({ resolved })
  },
}))

let stopWatching: (() => void) | null = null

/**
 * Applies the stored choice and follows the OS live while the choice is "system".
 * Called once from main.tsx; safe to call again.
 */
export function initTheme(): void {
  const { choice } = useTheme.getState()
  const resolved = resolveTheme(choice, systemPrefersDark())
  applyResolvedTheme(resolved)
  useTheme.setState({ resolved })
  stopWatching?.()
  stopWatching = watchSystemTheme((prefersDark) => useTheme.getState().syncWithSystem(prefersDark))
}
