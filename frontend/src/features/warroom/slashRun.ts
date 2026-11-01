import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { api, ApiError } from '../../lib/api'
import { HEALTH_KEY, useSystemHealth } from '../kill-switch/api'
import type { SettingsDoc } from '../onboarding/settings'
import type { WarroomThread } from './api'
import { sendMessage } from './live'
import { endOfBaghdadDay, type Slash } from './slash'
import { baghdadDay } from './time'
import { invalidateWatchlists, type ApiWatchlist, type WatchlistDay } from './watchlist'

/*
 * ATL-163: runs a parsed slash command against the existing APIs.
 *
 * - /pause → POST /api/system/pause; /resume opens the Resume confirmation (decision 251), which
 *   calls POST /api/system/resume. /kill and /kill flatten only open the Stop sheet (ATL-149) with
 *   close-all preselected for flatten; nothing is sent without the sheet's own confirmation.
 * - /cycle now → POST /api/cycles (cycle.start_requested, one a minute).
 * - /why, /research, /brief → owner messages: "/why <id>" verbatim (the Orchestrator answers it,
 *   ATL-161), and mentions of Technical + News or of the Orchestrator from the locale file.
 * - /pin → today's watchlist pin API; /block <instId> today → PATCH /api/settings adding the
 *   instrument to the blocklist with a blocklist_until expiry at 23:59:59 Baghdad today.
 */

export interface SlashNote {
  tone: 'info' | 'error'
  text: string
}

interface SettingsWithBlocklist extends SettingsDoc {
  settings: SettingsDoc['settings'] & { blocklist?: string[]; blocklist_until?: Record<string, string> | [] }
}

export function useSlashRunner(env: TradingEnv, thread: WarroomThread | undefined) {
  const { t } = useTranslation('warroom')
  const qc = useQueryClient()
  const health = useSystemHealth()
  const [kill, setKill] = useState({ open: false, flatten: false })
  const [resumeOpen, setResumeOpen] = useState(false)

  const info = (key: string, values?: Record<string, string>): SlashNote => ({ tone: 'info', text: t(key, values) })
  const fail = (key: string, values?: Record<string, string>): SlashNote => ({ tone: 'error', text: t(key, values) })

  function failure(e: unknown, tooSoon = 'slash.err.tooSoon'): SlashNote {
    if (!(e instanceof ApiError)) return fail('slash.err.network')
    if (e.status === 429) return fail(tooSoon)
    if (e.status === 503) return fail('slash.err.coreDown')
    return fail('slash.err.failed', { status: String(e.status) })
  }

  /** An owner message; the outbox shows it sending and offers a retry if it fails (ATL-40). */
  function say(content: string): null {
    if (thread) void sendMessage(qc, { env, threadId: thread.id, content })
    return null
  }

  async function block(instId: string): Promise<SlashNote> {
    const until = endOfBaghdadDay(Date.now())
    // One retry on 409: the settings moved between the read and the write.
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const doc = (await api.get<{ data: SettingsWithBlocklist }>(`/api/settings?env=${env}`)).data
      const list = doc.settings.blocklist ?? []
      const expiries = Array.isArray(doc.settings.blocklist_until) ? {} : (doc.settings.blocklist_until ?? {})
      if (list.includes(instId) && !expiries[instId]) return info('slash.done.alreadyBlocked', { inst: instId })
      try {
        await api.patch('/api/settings', {
          env,
          version: doc.version,
          changes: {
            blocklist: list.includes(instId) ? list : [...list, instId],
            blocklist_until: { ...expiries, [instId]: until },
          },
        })
        void qc.invalidateQueries({ queryKey: ['settings', env] })
        return info('slash.done.block', { inst: instId })
      } catch (e) {
        if (e instanceof ApiError && e.status === 409 && attempt === 0) continue
        if (e instanceof ApiError && e.status === 422) return fail('slash.err.blockInvalid', { inst: instId })
        return failure(e)
      }
    }
    return fail('slash.err.network')
  }

  /** Today's (Baghdad) watchlist pin API, as the rail's pin popover uses it (ATL-103). */
  async function pin(instId: string): Promise<SlashNote> {
    const today = baghdadDay(Date.now())
    const day = await api.get<WatchlistDay>(`/api/watchlists?date=${encodeURIComponent(today)}&env=${env}`)
    if (!day.watchlist) return fail('slash.err.noWatchlist')
    try {
      await api.post<{ watchlist: ApiWatchlist }>(`/api/watchlists/${encodeURIComponent(day.watchlist.id)}/pin`, { inst_id: instId })
      return info('slash.done.pin', { inst: instId })
    } catch (e) {
      if (!(e instanceof ApiError)) return failure(e)
      const code = typeof e.body === 'object' && e.body !== null ? (e.body as { error?: unknown }).error : null
      if (e.status === 409 && (code === 'edit_window_closed' || code === 'not_current_version')) return fail('slash.err.windowClosed')
      if (e.status === 422) {
        const msg = (e.fieldErrors.inst_id?.[0] ?? '').toLowerCase()
        const key = msg.includes('blocklist') ? 'blocked' : msg.includes('not live') ? 'notLive' : 'unknown'
        return fail(`slash.err.pin.${key}`, { inst: instId })
      }
      return failure(e)
    } finally {
      void invalidateWatchlists(qc, env)
    }
  }

  /** Runs the command; resolves with the line to show under the composer, or null for none. */
  async function run(s: Slash): Promise<SlashNote | null> {
    try {
      switch (s.kind) {
        case 'pause':
          await api.post('/api/system/pause', s.reason ? { reason: s.reason } : undefined)
          void qc.invalidateQueries({ queryKey: HEALTH_KEY })
          return info('slash.done.pause')
        case 'resume': {
          const data = health.data?.data
          if (!data) return fail('slash.err.status')
          if (!data.trading.paused && !data.breakers.some((b) => !b.self_clearing)) return info('slash.done.notPaused')
          setResumeOpen(true)
          return null
        }
        case 'kill':
          setKill({ open: true, flatten: s.flatten })
          return null
        case 'cycleNow':
          await api.post('/api/cycles')
          return info('slash.done.cycle')
        case 'why':
          return say(`/why ${s.id}`)
        case 'research':
          return say(t('slash.msg.research', { inst: s.instId }))
        case 'brief':
          return say(t('slash.msg.brief'))
        case 'pin':
          return await pin(s.instId)
        case 'block':
          return await block(s.instId)
        case 'usage':
        case 'unknown':
          return null
      }
    } catch (e) {
      return failure(e, s.kind === 'cycleNow' ? 'slash.err.cycleTooSoon' : 'slash.err.tooSoon')
    }
  }

  return {
    run,
    health: health.data?.data,
    kill,
    setKillOpen: (open: boolean) => setKill((k) => ({ ...k, open })),
    resumeOpen,
    setResumeOpen,
  }
}
