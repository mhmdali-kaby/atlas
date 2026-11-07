import { baghdadDay } from './time'

/*
 * ATL-163: the composer's slash commands (brief §12). Parsing is pure and lives here; the composer
 * runs the result (Composer.tsx `runSlash`). A message that starts with "/" is never sent as text,
 * except where a command is itself an owner message (/why, /research, /brief).
 */

export type SlashName = 'pause' | 'resume' | 'kill' | 'killFlatten' | 'cycleNow' | 'why' | 'research' | 'brief' | 'pin' | 'block'

export type Slash =
  | { kind: 'pause'; reason: string | null }
  | { kind: 'resume' }
  | { kind: 'kill'; flatten: boolean }
  | { kind: 'cycleNow' }
  | { kind: 'why'; id: string }
  | { kind: 'research'; instId: string }
  | { kind: 'brief' }
  | { kind: 'pin'; instId: string }
  | { kind: 'block'; instId: string }
  /** A known command with the wrong arguments: its usage line is shown. */
  | { kind: 'usage'; name: SlashName }
  /** Not a command: the whole list is shown. */
  | { kind: 'unknown'; typed: string }

export interface SlashDef {
  name: SlashName
  /** What the autocomplete writes into the composer. */
  insert: string
  /** What the list shows (arguments as placeholders). */
  label: string
}

/** Every command, in the order the autocomplete and the help list them. */
export const SLASH_COMMANDS: readonly SlashDef[] = [
  { name: 'pause', insert: '/pause', label: '/pause' },
  { name: 'resume', insert: '/resume', label: '/resume' },
  { name: 'kill', insert: '/kill', label: '/kill' },
  { name: 'killFlatten', insert: '/kill flatten', label: '/kill flatten' },
  { name: 'cycleNow', insert: '/cycle now', label: '/cycle now' },
  { name: 'why', insert: '/why ', label: '/why <id>' },
  { name: 'research', insert: '/research ', label: '/research <instId>' },
  { name: 'brief', insert: '/brief', label: '/brief' },
  { name: 'pin', insert: '/pin ', label: '/pin <instId>' },
  { name: 'block', insert: '/block ', label: '/block <instId> today' },
]

/** An OKX instId (BTC-USDT-SWAP), the same shape the settings API accepts. */
const INST_ID = /^[A-Z0-9]+(-[A-Z0-9]+)+$/
/** A proposal or order id: a UUID or an OKX numeric id. */
const REF_ID = /^[A-Za-z0-9-]{1,64}$/

/** Whether the text is a slash command at all (it starts with "/"). */
export const isSlash = (text: string): boolean => text.trimStart().startsWith('/')

/** Parses a message that starts with "/". */
export function parseSlash(text: string): Slash {
  const words = text.trim().split(/\s+/)
  const head = (words[0] ?? '').slice(1).toLowerCase()
  const args = words.slice(1)
  const inst = (s: string | undefined) => (s && INST_ID.test(s.toUpperCase()) ? s.toUpperCase() : null)

  switch (head) {
    case 'pause':
      return { kind: 'pause', reason: args.length ? args.join(' ') : null }
    case 'resume':
      return args.length ? { kind: 'usage', name: 'resume' } : { kind: 'resume' }
    case 'kill':
      if (!args.length) return { kind: 'kill', flatten: false }
      if (args.length === 1 && args[0]!.toLowerCase() === 'flatten') return { kind: 'kill', flatten: true }
      return { kind: 'usage', name: 'killFlatten' }
    case 'cycle':
      return args.length === 1 && args[0]!.toLowerCase() === 'now' ? { kind: 'cycleNow' } : { kind: 'usage', name: 'cycleNow' }
    case 'why':
      return args.length === 1 && REF_ID.test(args[0]!) ? { kind: 'why', id: args[0]! } : { kind: 'usage', name: 'why' }
    case 'research': {
      const id = args.length === 1 ? inst(args[0]) : null
      return id ? { kind: 'research', instId: id } : { kind: 'usage', name: 'research' }
    }
    case 'brief':
      return args.length ? { kind: 'usage', name: 'brief' } : { kind: 'brief' }
    case 'pin': {
      const id = args.length === 1 ? inst(args[0]) : null
      return id ? { kind: 'pin', instId: id } : { kind: 'usage', name: 'pin' }
    }
    case 'block': {
      const id = args.length === 2 && args[1]!.toLowerCase() === 'today' ? inst(args[0]) : null
      return id ? { kind: 'block', instId: id } : { kind: 'usage', name: 'block' }
    }
    default:
      return { kind: 'unknown', typed: words[0] ?? '/' }
  }
}

/**
 * The commands the autocomplete offers for what is typed: only while the text is a single
 * "/word" (or a command's first word plus the start of its fixed second word, "/kill fl").
 */
export function slashMatches(text: string): SlashDef[] {
  if (!/^\/[A-Za-z]*( [A-Za-z]*)?$/.test(text)) return []
  const q = text.toLowerCase()
  return SLASH_COMMANDS.filter((c) => c.insert.toLowerCase().startsWith(q))
}

/** The end of today's Baghdad day, 23:59:59 at UTC+3 (Baghdad has no DST), as ISO 8601. */
export function endOfBaghdadDay(now: number): string {
  return `${baghdadDay(now)}T23:59:59+03:00`
}
