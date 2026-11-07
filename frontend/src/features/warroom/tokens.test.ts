import { describe, expect, it } from 'vitest'
import { continues, shapeOf } from './shape'
import { baghdadDay, formatTime } from './time'
import { excerptSource, parseBlocks, tokenize, type Piece } from './tokens'
import type { WarroomMessage } from './api'

/** The islands of a line, as text (design v4 War Room §9 test cases). */
const islands = (src: string) =>
  tokenize(src)
    .flatMap((p): Piece[] => (typeof p === 'object' && p.type === 'strong' ? p.children : [p]))
    .filter((p): p is Exclude<Piece, string> => typeof p !== 'string')
    .map((p) => ('text' in p ? p.text : p.type === 'tag' ? p.tag : ''))

describe('War Room tokenizer (§9 test cases)', () => {
  it.each([
    ['مركز شراء TSLA-USDT-SWAP عند −3.6% على الهامش (−0.6R)', ['TSLA-USDT-SWAP', '−3.6%', '−0.6R']],
    ['بزيادة $1,842.20 (+0.04%)', ['$1,842.20', '+0.04%']],
    ['والتمويل −$12.30', ['−$12.30']],
    ['above 4,180, and not 4,200', ['4,180', '4,200']],
    ['range 4,120–4,180; 0.5-1.5 ATR', ['4,120–4,180', '0.5-1.5 ATR']],
    ['0.55 × 2.1 − 0.45 = 0.71', ['0.55 × 2.1 − 0.45 = 0.71']],
    ['n = 41', ['n = 41']],
    ['+1.4R −1.0R', ['+1.4R', '−1.0R']],
    ["It's -0.4R", ['−0.4R']],
    ['importance-5, US100, Q3', []],
  ])('%s', (text, expected) => {
    expect(islands(text)).toEqual(expected)
  })

  it('keeps the brackets and the sentence comma outside the islands', () => {
    const pieces = tokenize('(−0.6R)')
    expect(pieces[0]).toBe('(')
    expect(pieces.at(-1)).toBe(')')
    expect(tokenize('above 4,180, and').at(-1)).toBe(', and')
  })

  it('isolates code, known mentions, status tags and bold', () => {
    const pieces = tokenize('**Brief** {{CONFIRMED}} via `okx_market.get_candles` for @Historian and @nobody at 15:30')
    expect(pieces[0]).toEqual({ type: 'strong', children: ['Brief'] })
    expect(pieces).toContainEqual({ type: 'tag', tag: 'CONFIRMED' })
    expect(pieces).toContainEqual({ type: 'code', text: 'okx_market.get_candles' })
    expect(pieces).toContainEqual({ type: 'mention', text: '@Historian' })
    expect(pieces).toContainEqual({ type: 'fig', text: '15:30' })
    expect(pieces.join('')).toContain('@nobody')
  })

  it('parses paragraphs and lists, and makes one-line excerpts', () => {
    expect(parseBlocks('Watchlist:\n1. **ETH-USDT-SWAP**, long\n2. SOL\n\n- a\n- b')).toEqual([
      { type: 'p', text: 'Watchlist:' },
      { type: 'ol', items: ['**ETH-USDT-SWAP**, long', 'SOL'] },
      { type: 'ul', items: ['a', 'b'] },
    ])
    expect(excerptSource('Picks:\n1. ETH\n2. SOL')).toBe('Picks: ETH SOL')
  })
})

describe('War Room times and grouping', () => {
  const msg = (over: Partial<WarroomMessage>): WarroomMessage => ({
    id: 'x',
    thread_id: 't',
    author_type: 'agent',
    author: 'historian',
    kind: 'text',
    content_md: '',
    card: null,
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    created_at: '2026-10-28T10:00:00Z',
    ...over,
  })

  it('shows times and days in Asia/Baghdad (UTC+3)', () => {
    expect(formatTime('2026-10-28T21:30:00Z', 'en')).toBe('00:30')
    expect(formatTime('2026-10-28T21:30:00Z', 'ar')).toBe('00:30')
    expect(baghdadDay('2026-10-28T20:59:59Z')).toBe('2026-10-28')
    expect(baghdadDay('2026-10-28T21:00:00Z')).toBe('2026-10-29')
  })

  it('draws each kind with its own shape', () => {
    expect(shapeOf(msg({ author_type: 'human' }))).toBe('owner')
    expect(shapeOf(msg({ author_type: 'system', author: 'trading_core' }))).toBe('event')
    expect(shapeOf(msg({ author_type: 'system', kind: 'system_alert' }))).toBe('alert')
    expect(shapeOf(msg({ kind: 'watchlist' }))).toBe('agent')
  })

  it('groups the same author within 5 minutes on one Baghdad day, never a reply', () => {
    const a = msg({})
    expect(continues(msg({ created_at: '2026-10-28T10:04:00Z' }), a)).toBe(true)
    expect(continues(msg({ created_at: '2026-10-28T10:05:00Z' }), a)).toBe(false)
    expect(continues(msg({ created_at: '2026-10-28T10:01:00Z', author: 'strategist' }), a)).toBe(false)
    expect(continues(msg({ created_at: '2026-10-28T10:01:00Z', reply_to: 'x' }), a)).toBe(false)
    expect(continues(msg({ created_at: '2026-10-28T10:01:00Z', kind: 'watchlist' }), a)).toBe(false)
  })
})
