import { describe, expect, it } from 'vitest'
import { ApiError } from '../../lib/api'
import type { SettingsDoc } from '../onboarding'
import { changesFor, current, displayHost, listProblems, normaliseHost, numError, parseCount, pruned, savedFrom, serverErrors, stepped, withEdit } from './model'

const doc = (settings: Record<string, unknown>) => ({ id: 'x', env: 'demo', version: 4, settings, presets: {}, updated_at: '' }) as unknown as SettingsDoc

describe('savedFrom', () => {
  it('reads the flat dotted keys and falls back to the defaults for a field an old row lacks', () => {
    const s = savedFrom(doc({ 'agents.market_scout.enabled': false, 'agents.market_scout.daily_tokens': 500000, 'agents.web.deny_domains': ['x.com'] }))
    expect(s['agents.market_scout.enabled']).toBe(false)
    expect(s['agents.market_scout.daily_tokens']).toBe(500000)
    expect(s['agents.market_scout.model']).toBe('strong')
    expect(s['agents.orchestrator.model']).toBe('standard')
    expect(s['agents.historian.enabled']).toBe(true)
    expect(s['agents.orchestrator.enabled']).toBe(false)
    expect(s['agents.historian.daily_turns']).toBe(300)
    expect(s['agents.web.allow_domains']).toEqual([])
    expect(s['agents.web.deny_domains']).toEqual(['x.com'])
  })
})

describe('edits', () => {
  const saved = savedFrom(doc({ 'agents.market_scout.enabled': true, 'agents.market_scout.daily_tokens': 3000000, 'agents.web.deny_domains': ['x.com'] }))

  it('drops an edit back to the saved value', () => {
    let e = withEdit(saved, {}, 'agents.market_scout.enabled', false)
    expect(e).toEqual({ 'agents.market_scout.enabled': false })
    e = withEdit(saved, e, 'agents.market_scout.enabled', true)
    expect(e).toEqual({})
    expect(withEdit(saved, {}, 'agents.market_scout.daily_tokens', '3,000,000')).toEqual({})
    expect(withEdit(saved, {}, 'agents.web.deny_domains', ['x.com'])).toEqual({})
  })

  it('sends booleans, integers and whole lists', () => {
    let e = withEdit(saved, {}, 'agents.market_scout.enabled', false)
    e = withEdit(saved, e, 'agents.market_scout.daily_tokens', '2,500,000')
    e = withEdit(saved, e, 'agents.web.deny_domains', ['x.com', 'reddit.com'])
    expect(changesFor(saved, e)).toEqual({
      'agents.market_scout.enabled': false,
      'agents.market_scout.daily_tokens': 2500000,
      'agents.web.deny_domains': ['x.com', 'reddit.com'],
    })
  })

  it('keeps only the edits that still differ from a newer saved version (409)', () => {
    const newer = savedFrom(doc({ 'agents.market_scout.enabled': false }))
    expect(pruned(newer, { 'agents.market_scout.enabled': false, 'agents.historian.model': 'routine' })).toEqual({ 'agents.historian.model': 'routine' })
  })

  it('shows saved numbers grouped', () => {
    expect(current(saved, {}, 'agents.market_scout.daily_tokens', 'en')).toBe('3,000,000')
  })
})

describe('numbers', () => {
  it('parses grouped digits only', () => {
    expect(parseCount('3,000,000')).toBe(3000000)
    expect(parseCount('3 000')).toBe(3000)
    expect(parseCount('1.5')).toBeNull()
    expect(parseCount('-1')).toBeNull()
  })

  it('checks the ATL-113 bounds', () => {
    expect(numError('agents.x.daily_tokens', '')).toBe('empty')
    expect(numError('agents.x.daily_tokens', '0')).toBe('range')
    expect(numError('agents.x.daily_tokens', '1000000001')).toBe('range')
    expect(numError('agents.x.daily_turns', '100000')).toBeNull()
    expect(numError('agents.x.daily_turns', '100001')).toBe('range')
  })

  it('steps on the grid inside the bounds', () => {
    expect(stepped('agents.x.daily_tokens', '3,000,000', 1)).toBe(3100000)
    expect(stepped('agents.x.daily_tokens', '3,050,000', -1)).toBe(3000000)
    expect(stepped('agents.x.daily_turns', '5', -1)).toBe(1)
    expect(stepped('agents.x.daily_turns', '100000', 1)).toBe(100000)
  })
})

describe('site names', () => {
  it('normalises like the server and keeps bad entries as typed', () => {
    expect(normaliseHost('  CoinDesk.com. ')).toBe('coindesk.com')
    expect(normaliseHost('http://x/')).toBe('http://x/')
    expect(normaliseHost('')).toBe('')
    const puny = normaliseHost('Bücher.example')
    expect(puny).toBe('xn--bcher-kva.example')
    expect(displayHost(puny)).toBe('bücher.example')
  })
})

describe('server answers', () => {
  it('ties a list entry error to its host so it follows the entry', () => {
    const err = new ApiError(422, 'bad', {
      errors: {
        'changes.agents.web.deny_domains.1': ['Each entry must be a bare hostname.'],
        'changes.agents.historian.daily_tokens': ['Too big.'],
      },
    })
    const errors = serverErrors(err, { 'agents.web.deny_domains': ['x.com', 'http://x/'] })
    expect(errors).toEqual({
      'agents.web.deny_domains.1': { message: 'Each entry must be a bare hostname.', host: 'http://x/' },
      'agents.historian.daily_tokens': { message: 'Too big.' },
    })
    expect(listProblems(errors, 'deny', ['http://x/'])).toEqual({ bad: new Set(['http://x/']), message: 'Each entry must be a bare hostname.' })
    expect(listProblems(errors, 'deny', ['x.com'])).toEqual({ bad: new Set(), message: null })
    expect(serverErrors(new ApiError(500, 'x'), {})).toEqual({})
  })
})
