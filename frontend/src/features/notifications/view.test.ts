import { describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import type { NotificationRow } from './api'
import { isEventId } from './events'
import { viewOf } from './view'

function row(event: string, severity: string, params: Record<string, unknown>): NotificationRow {
  return {
    id: 'n1',
    env: 'demo',
    event,
    severity,
    title_key: `notifications.events.${event}.title`,
    params,
    link: '/agents',
    channels: { in_app: true, browser: true, telegram: false },
    read_at: null,
    created_at: '2026-11-14T21:00:00Z',
  } as NotificationRow
}

describe('Claude usage limit notifications', () => {
  it('are known events, so Settings → Notifications lists them', () => {
    expect(isEventId('claude_limit_reached')).toBe(true)
    expect(isEventId('claude_limit_cleared')).toBe(true)
  })

  it('say the agents are paused, when Atlas checks again and when the limit resets', () => {
    const t = i18n.getFixedT('en', 'notifications')
    const v = viewOf(row('claude_limit_reached', 'warning', { resets_at: '2026-11-14T22:00:00+00:00', probe_minutes: 10 }), t, 'en')
    expect(v.title).toBe('Claude usage limit reached')
    expect(v.body).toMatch(/agents are paused/)
    expect(v.body).toMatch(/every ⁦10⁩ minutes/)
    expect(v.body).toMatch(/resets/)
    expect(v.tone).toBe('warning')
    expect(v.action).toBe('Open Agents')
  })

  it('without a known reset time still says Atlas keeps checking', () => {
    const t = i18n.getFixedT('en', 'notifications')
    const v = viewOf(row('claude_limit_reached', 'warning', { resets_at: null, probe_minutes: 10 }), t, 'en')
    expect(v.body).toMatch(/every ⁦10⁩ minutes/)
    expect(v.body).not.toMatch(/—/)
  })

  it('announce the resume', () => {
    const t = i18n.getFixedT('en', 'notifications')
    const v = viewOf(row('claude_limit_cleared', 'info', { resets_at: null, probe_minutes: 10 }), t, 'en')
    expect(v.title).toBe('Claude is answering again')
    expect(v.body).toMatch(/resumed/)
  })

  it('read in Arabic', () => {
    const t = i18n.getFixedT('ar', 'notifications')
    const reached = viewOf(row('claude_limit_reached', 'warning', { resets_at: null, probe_minutes: 10 }), t, 'ar')
    const cleared = viewOf(row('claude_limit_cleared', 'info', {}), t, 'ar')
    expect(reached.title).toBe('بلغ Claude حد الاستخدام')
    expect(cleared.title).toBe('عاد Claude للعمل')
    expect(reached.body).not.toMatch(/[A-Za-z]{4,}/u)
  })
})
