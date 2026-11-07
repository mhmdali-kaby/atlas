import { agentByHandle } from './agents'

/*
 * Bidi isolation for free text (design v4 War Room §9). Agents write content_md freely, so the
 * renderer — never the data — wraps every code span, @mention, instrument ID and figure in a
 * <bdi dir="ltr"> island. Without it "(−0.6R)" in an Arabic sentence renders as "(0.6R−)".
 * Tokens are matched on the raw text, in the order code → status tag → mention → instrument →
 * figure; React escapes the rest.
 */

const NUM = String.raw`(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)`
const ATOM = String.raw`(?:\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?|\d{1,2}(?::\d{2}){1,2}|×?[+−-]?[$€]?${NUM}(?:%|R|×|x|σ| ?bps| ATR|bn|[kMB]|[mhHdDW])?)`
const JOIN = String.raw`(?:\s?–\s?|[-−/]|\s[-−×=/→]\s)`
export const FIGURE = new RegExp(String.raw`(?<![\w.,:$€+×−-])(?:[A-Za-z]\s?=\s?)?${ATOM}(?:${JOIN}${ATOM})*(?![\w$€])`, 'g')
export const INSTRUMENT = /\b[A-Z0-9]{2,12}-(?:USDT|USDC|USD)(?:-SWAP|-\d{6}(?:-\d+(?:\.\d+)?-[CP])?)?\b/g
const TAG = /\{\{(CONFIRMED|REPORTED|RUMOUR)\}\}/g
const MENTION = /(^|[^\w@])@([A-Za-z_]+)/g
const CODE = /`([^`]+)`/g

// Placeholders: private-use characters, which are never digits, letters or @.
const OPEN = String.fromCharCode(0xe000)
const CLOSE = String.fromCharCode(0xe001)
const SLOT_SPLIT = new RegExp(`(${OPEN}[\\uE100-\\uF8FF]${CLOSE})`)

export type Token =
  | { type: 'code'; text: string }
  | { type: 'tag'; tag: 'CONFIRMED' | 'REPORTED' | 'RUMOUR' }
  | { type: 'mention'; text: string }
  | { type: 'inst'; text: string }
  | { type: 'fig'; text: string }

export type Piece = string | Token | { type: 'strong'; children: Array<string | Token> }

/** A leading hyphen-minus sign is drawn as − (U+2212), like SignedValue. */
const typographicSign = (figure: string) => figure.replace(/^(×?)-(?=[$€\d])/, '$1−')

/**
 * Splits one line of free text into plain strings and islands. `**bold**` becomes a strong
 * piece; with `flat` (excerpts, status lines) bold markers are dropped instead.
 */
export function tokenize(src: string, flat = false): Piece[] {
  const kept: Token[] = []
  const hold = (token: Token) => OPEN + String.fromCharCode(0xe100 + kept.push(token) - 1) + CLOSE
  let s = String(src)
  s = s.replace(CODE, (_m, code: string) => hold({ type: 'code', text: code }))
  s = s.replace(TAG, (_m, tag: 'CONFIRMED' | 'REPORTED' | 'RUMOUR') => hold({ type: 'tag', tag }))
  s = s.replace(MENTION, (m, pre: string, handle: string) => {
    const agent = agentByHandle(handle)
    return agent ? pre + hold({ type: 'mention', text: `@${handle}` }) : m
  })
  s = s.replace(INSTRUMENT, (id) => hold({ type: 'inst', text: id }))
  s = s.replace(FIGURE, (f) => hold({ type: 'fig', text: typographicSign(f) }))

  const expand = (text: string): Array<string | Token> =>
    text
      .split(SLOT_SPLIT)
      .filter((part) => part !== '')
      .map((part) => {
        const m = new RegExp(`^${OPEN}([\\uE100-\\uF8FF])${CLOSE}$`).exec(part)
        return m ? kept[m[1]!.charCodeAt(0) - 0xe100]! : part
      })

  if (flat) return expand(s.replace(/\*\*/g, ''))
  const out: Piece[] = []
  const bold = /\*\*([^*]+)\*\*/g
  let last = 0
  for (let m = bold.exec(s); m; m = bold.exec(s)) {
    out.push(...expand(s.slice(last, m.index)))
    out.push({ type: 'strong', children: expand(m[1]!) })
    last = m.index + m[0].length
  }
  out.push(...expand(s.slice(last)))
  return out
}

/** Text without islands or markers, for the clipboard, labels and the live region. */
export function plainText(src: string, tagLabel: (tag: string) => string): string {
  return String(src)
    .replace(TAG, (_m, k: string) => tagLabel(k))
    .replace(/\*\*|`/g, '')
    .replace(/\s*\n+\s*/g, ' ')
    .trim()
}

/** One line of a message for reply quotes: list markers dropped, lines joined. */
export function excerptSource(src: string): string {
  return String(src)
    .replace(/^\s*(?:-|\d+\.)\s+/gm, '')
    .replace(/\s*\n+\s*/g, ' ')
    .trim()
}

export type Block = { type: 'p'; text: string } | { type: 'ul' | 'ol'; items: string[] }

/** Paragraphs and `-` / `1.` lists (design §12: the content_md subset agents write). */
export function parseBlocks(src: string): Block[] {
  const blocks: Block[] = []
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null
  const flush = () => {
    if (list) blocks.push(list)
    list = null
  }
  for (const raw of String(src).split('\n')) {
    const line = raw.replace(/\s+$/, '')
    const ul = /^\s*- (.*)$/.exec(line)
    const ol = /^\s*\d+\. (.*)$/.exec(line)
    const item = ul ?? ol
    if (item) {
      const type = ul ? 'ul' : 'ol'
      if (!list || list.type !== type) {
        flush()
        list = { type, items: [] }
      }
      list.items.push(item[1]!)
      continue
    }
    flush()
    if (line.trim()) blocks.push({ type: 'p', text: line })
  }
  flush()
  return blocks
}

/** A translated sentence split around one \u0000 slot, so the slot can be an element (an island). */
export function around(text: string): [string, string] {
  const [before = '', after = ''] = text.split('\u0000')
  return [before, after]
}
