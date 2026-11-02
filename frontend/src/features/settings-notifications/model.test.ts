import { describe, expect, it } from 'vitest'
import type { SettingsDoc } from '../onboarding'
import { EVENTS } from '../notifications/events'
import { CHANNELS, NEEDS, masked, prefKey, prefsFrom, validateBot } from './model'

const doc = (settings: Record<string, unknown>) => ({ id: 'x', env: 'demo', version: 3, settings, presets: {}, updated_at: '' }) as unknown as SettingsDoc

describe('settings-notifications model', () => {
  it('covers the 11 brief §17 events × 3 channels as notifications.<event>.<channel>', () => {
    expect(EVENTS).toHaveLength(11)
    expect(prefKey('order_filled', 'browser')).toBe('notifications.order_filled.browser')
    expect(Object.keys(prefsFrom(doc({})))).toHaveLength(33)
  })

  it('reads saved booleans and falls back to in_app/browser on, telegram off', () => {
    const p = prefsFrom(doc({ 'notifications.order_filled.browser': false, 'notifications.stop_hit.telegram': true }))
    expect(p['notifications.order_filled.browser']).toBe(false)
    expect(p['notifications.stop_hit.telegram']).toBe(true)
    for (const ch of CHANNELS) expect(p[prefKey('daily_report', ch)]).toBe(ch !== 'telegram')
  })

  it('the four "Needs you" events are the first group', () => {
    expect(NEEDS).toEqual(['proposal_awaiting_approval', 'circuit_breaker', 'kill_switch', 'reconciliation_mismatch'])
  })

  it('validates the bot token and chat id with ATL-215’s rules', () => {
    expect(validateBot('', '')).toEqual({ token: 'tokenEmpty', chat: 'chatEmpty' })
    expect(validateBot('123:short', 'abc')).toEqual({ token: 'token', chat: 'chat' })
    expect(validateBot('123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsWXYZ', '-1001234')).toEqual({})
  })

  it('masks a saved value as one LTR isolate', () => {
    expect(masked('WXYZ')).toBe('⁦•••• WXYZ⁩')
  })
})
