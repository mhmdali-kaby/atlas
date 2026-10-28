import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { THEME_STORAGE_KEY, readStoredChoice, resolveTheme } from './theme'

type Listener = (event: MediaQueryListEvent) => void

/** A controllable prefers-color-scheme media query for jsdom. */
function mockSystemDark(initial: boolean) {
  let matches = initial
  const listeners = new Set<Listener>()
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() {
      return matches
    },
    media: query,
    addEventListener: (_: string, l: Listener) => listeners.add(l),
    removeEventListener: (_: string, l: Listener) => listeners.delete(l),
  })) as unknown as typeof window.matchMedia
  return {
    set(next: boolean) {
      matches = next
      for (const l of listeners) l({ matches: next } as MediaQueryListEvent)
    },
  }
}

const html = document.documentElement

beforeEach(() => {
  localStorage.clear()
  html.classList.remove('dark')
  vi.resetModules()
})

describe('resolveTheme', () => {
  it('follows the OS only in system mode', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  it('treats a missing or unknown stored value as system', () => {
    expect(readStoredChoice()).toBe('system')
    localStorage.setItem(THEME_STORAGE_KEY, 'purple')
    expect(readStoredChoice()).toBe('system')
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    expect(readStoredChoice()).toBe('dark')
  })
})

describe('useTheme store', () => {
  it('applies and persists an explicit choice, and removes the key for system', async () => {
    mockSystemDark(false)
    const { useTheme, initTheme } = await import('./useTheme')
    initTheme()
    expect(html.classList.contains('dark')).toBe(false)

    useTheme.getState().setChoice('dark')
    expect(html.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    useTheme.getState().setChoice('system')
    expect(html.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })

  it('follows OS changes live in system mode and ignores them otherwise', async () => {
    const os = mockSystemDark(false)
    const { useTheme, initTheme } = await import('./useTheme')
    initTheme()

    os.set(true)
    expect(html.classList.contains('dark')).toBe(true)
    expect(useTheme.getState().resolved).toBe('dark')

    useTheme.getState().setChoice('light')
    os.set(false)
    os.set(true)
    expect(html.classList.contains('dark')).toBe(false)
  })

  it('starts from the stored choice', async () => {
    mockSystemDark(false)
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    const { useTheme, initTheme } = await import('./useTheme')
    initTheme()
    expect(useTheme.getState().choice).toBe('dark')
    expect(html.classList.contains('dark')).toBe(true)
  })
})

describe('index.html no-flash script', () => {
  const source = (() => {
    const file = readFileSync(resolve(__dirname, '../../index.html'), 'utf8')
    const match = /<script id="atlas-theme-init">([\s\S]*?)<\/script>/.exec(file)
    if (!match?.[1]) throw new Error('index.html lost its atlas-theme-init script')
    return match[1]
  })()
  const run = () => new Function(source)()

  it.each([
    ['dark', false, true],
    ['light', true, false],
    [null, true, true],
    [null, false, false],
  ] as const)('stored %s with OS dark=%s gives dark=%s, the same as resolveTheme', (stored, osDark, expected) => {
    mockSystemDark(osDark)
    if (stored) localStorage.setItem(THEME_STORAGE_KEY, stored)
    run()
    expect(html.classList.contains('dark')).toBe(expected)
    expect(resolveTheme(readStoredChoice(), osDark) === 'dark').toBe(expected)
  })
})
