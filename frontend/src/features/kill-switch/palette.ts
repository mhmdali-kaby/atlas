import { useQueryClient } from '@tanstack/react-query'
import { CirclePause, CirclePlay, OctagonX, RefreshCw } from 'lucide-react'
import i18n from '../../i18n'
import { api, ApiError } from '../../lib/api'
import { toast } from '../../ui'
import type { PaletteEntry, PaletteModule } from '../command-palette/types'
import { HEALTH_KEY, useSystemHealth } from './api'
import { useKillSheets } from './sheetStore'

/*
 * The trading actions in the ⌘K palette (ATL-249, design v22 §6). Pause runs at once (it only
 * holds new trades). Resume and Stop only open their sheets, so nothing is resumed, cancelled or
 * closed until the owner confirms there. Run a cycle now calls the cycle API once, as /cycle now does.
 */

const NS = 'command-palette'
const tr = (key: string) => i18n.t(`${NS}:${key}`)

function failure(e: unknown, titleKey: string, tooSoonKey?: string) {
  const body = e instanceof ApiError ? (e.status === 429 && tooSoonKey ? tr(tooSoonKey) : undefined) : tr('err.network')
  toast({ title: tr(titleKey), body, tone: 'error' })
}

function useTradingActions(): PaletteEntry[] {
  const qc = useQueryClient()
  const health = useSystemHealth().data?.data
  const setKill = useKillSheets((s) => s.setKill)
  const setResume = useKillSheets((s) => s.setResume)
  // Resume is offered while trading is held: paused by the owner, or a breaker only Resume clears.
  const held = health ? health.trading.paused || health.breakers.some((b) => !b.self_clearing) : false

  const pause: PaletteEntry = {
    id: 'action.pause',
    group: 'actions',
    order: 10,
    icon: CirclePause,
    labelKey: `${NS}:a.pause`,
    subKey: `${NS}:a.pauseSub`,
    keywords: 'pause hold /pause ايقاف مؤقت',
    run: async () => {
      try {
        await api.post('/api/system/pause')
        toast({ title: tr('done.paused'), body: tr('done.pausedBody'), tone: 'success' })
      } catch (e) {
        failure(e, 'err.pause')
      } finally {
        void qc.invalidateQueries({ queryKey: HEALTH_KEY })
      }
    },
  }
  const resume: PaletteEntry = {
    id: 'action.resume',
    group: 'actions',
    order: 10,
    icon: CirclePlay,
    labelKey: `${NS}:a.resume`,
    subKey: `${NS}:a.resumeSub`,
    keywords: 'resume start unpause /resume استئناف',
    run: () => setResume(true),
  }
  const stop: PaletteEntry = {
    id: 'action.stop',
    group: 'actions',
    order: 20,
    tone: 'stop',
    icon: OctagonX,
    labelKey: `${NS}:a.stop`,
    subKey: `${NS}:a.stopSub`,
    keywords: 'stop kill flatten emergency /kill ايقاف طوارئ',
    run: () => setKill(true),
  }
  const cycle: PaletteEntry = {
    id: 'action.cycle',
    group: 'actions',
    order: 30,
    icon: RefreshCw,
    labelKey: `${NS}:a.cycle`,
    subKey: `${NS}:a.cycleSub`,
    keywords: 'cycle now analyse analyze /cycle دورة تحليل',
    run: async () => {
      try {
        await api.post('/api/cycles')
        toast({ title: tr('done.cycled'), body: tr('done.cycledBody'), tone: 'success' })
      } catch (e) {
        failure(e, 'err.cycle', 'err.tooSoon')
      }
    },
  }
  // Until health has loaded neither Pause nor Resume is offered: the palette never guesses the state.
  return [...(health ? [held ? resume : pause] : []), stop, cycle]
}

const palette: PaletteModule = { useEntries: useTradingActions }

export default palette
