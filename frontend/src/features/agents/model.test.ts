import { describe, expect, it } from 'vitest'
import type { AgentRow, ClaudeStatus } from './api'
import { claudeView, cliVersion, compact, dayOf, keyVars, medalStatus, meter, shownState, sumNotional } from './model'

function row(over: Partial<AgentRow> = {}): AgentRow {
  return {
    slug: 'historian',
    name: 'Historian',
    handle: 'Historian',
    enabled: true,
    model: 'strong',
    model_name: 'opus',
    state: 'idle',
    state_since: null,
    current_task: null,
    today: { tokens: 0, tokens_in: 0, tokens_out: 0, turns: 0, runs: 0, cost_usd_notional: '0.000000' },
    budget: { daily_tokens: 1000, daily_turns: 10 },
    prompt: null,
    last_run: null,
    ...over,
  }
}

function claude(over: Partial<ClaudeStatus> = {}): ClaudeStatus {
  return {
    ok: true,
    installed: true,
    logged_in: true,
    api_key_env_clear: true,
    login_command: 'claude login',
    checks: [
      { name: 'claude_cli', ok: true, status: 'pass', detail: 'Installed (2.4.1 (Claude Code))' },
      { name: 'claude_cli_login', ok: true, status: 'pass', detail: 'Logged in' },
      { name: 'claude_api_key', ok: true, status: 'pass', detail: 'No key' },
    ],
    checked_at: '2026-10-30T12:00:00Z',
    ...over,
  }
}

describe('meter', () => {
  it('warns from 80% and caps at 100', () => {
    expect(meter(790, 1000)).toMatchObject({ pct: 79, warn: false, full: false })
    expect(meter(850, 1000)).toMatchObject({ pct: 85, warn: true, full: false })
    expect(meter(1200, 1000)).toMatchObject({ pct: 100, warn: true, full: true, fill: 100 })
  })
  it('shows a sliver for a little use and nothing for none', () => {
    expect(meter(1, 1000).fill).toBe(2)
    expect(meter(0, 1000).fill).toBe(0)
    expect(meter(5, 0)).toMatchObject({ pct: 0, warn: false })
  })
})

describe('shownState', () => {
  it('reports disabled whenever the agent is off', () => {
    expect(shownState(row({ enabled: false, state: 'thinking' }))).toBe('disabled')
  })
  it('reports over_budget once either daily budget is reached', () => {
    expect(shownState(row({ today: { ...row().today, tokens: 1000 } }))).toBe('over_budget')
    expect(shownState(row({ today: { ...row().today, turns: 10 } }))).toBe('over_budget')
  })
  it('keeps the API state otherwise, idle for an unknown one', () => {
    expect(shownState(row({ state: 'rate_limited' }))).toBe('rate_limited')
    expect(shownState(row({ state: 'sleeping' }))).toBe('idle')
  })
  it('maps states to the five medallion statuses', () => {
    expect(medalStatus('disabled')).toBe('offline')
    expect(medalStatus('rate_limited')).toBe('degraded')
    expect(medalStatus('over_budget')).toBe('degraded')
    expect(medalStatus('waiting')).toBe('waiting')
  })
})

describe('figures', () => {
  it('compacts token counts', () => {
    expect(compact(3_000_000, 'en')).toBe('3M')
    expect(compact(1_500_000, 'en')).toBe('1.5M')
    expect(compact(412_300, 'en')).toBe('412K')
    expect(compact(12_400, 'en')).toBe('12.4K')
    expect(compact(950, 'ar')).toBe('950')
  })
  it('adds notional costs exactly as decimal strings', () => {
    expect(sumNotional(['0.100000', '0.200000', '0.000001'])).toBe('0.300001')
    expect(sumNotional([])).toBe('0.000000')
  })
  it('tells today, yesterday and earlier on the Baghdad calendar', () => {
    const now = Date.parse('2026-10-30T22:30:00Z') // 01:30 on the 31st in Baghdad
    expect(dayOf(Date.parse('2026-10-30T21:10:00Z'), now)).toBe('today')
    expect(dayOf(Date.parse('2026-10-30T20:50:00Z'), now)).toBe('yesterday')
    expect(dayOf(Date.parse('2026-10-28T10:00:00Z'), now)).toBe('earlier')
  })
})

describe('Claude status', () => {
  it('reads the CLI version and key variable names from the check details', () => {
    expect(cliVersion(claude())).toBe('2.4.1')
    const withKey = claude({
      api_key_env_clear: false,
      checks: [{ name: 'claude_api_key', ok: false, status: 'fail', detail: 'ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN are set.' }],
    })
    expect(keyVars(withKey)).toEqual(['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'])
  })
  it('picks the panel by the first problem', () => {
    expect(claudeView(claude())).toBe('ok')
    expect(claudeView(claude({ ok: false, installed: false, logged_in: false }))).toBe('missing')
    expect(claudeView(claude({ ok: false, logged_in: false, api_key_env_clear: false }))).toBe('logged_out')
    expect(claudeView(claude({ ok: false, api_key_env_clear: false }))).toBe('api_key')
  })
})
