import { useLocation } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { useThreads, type WarroomThread } from './api'

/** The thread a War Room URL points at: /warroom is Main, /warroom/threads/<id> any thread. */
export function threadIdFromPath(pathname: string): string | null {
  const m = /^\/warroom\/threads\/([^/]+)\/?$/.exec(pathname)
  return m ? decodeURIComponent(m[1]!) : null
}

export function threadPath(thread: Pick<WarroomThread, 'id' | 'kind'>): string {
  return thread.kind === 'main' ? '/warroom' : `/warroom/threads/${encodeURIComponent(thread.id)}`
}

/**
 * The thread list and the selected thread. Read from the URL (not useParams), so the sidebar
 * sections — rendered by the shell, outside the page's route — agree with the page.
 */
export function useSelectedThread() {
  const env = useTradingEnv()
  const { pathname } = useLocation()
  const threads = useThreads(env)
  const list = threads.data?.data ?? []
  const wanted = threadIdFromPath(pathname)
  const selected: WarroomThread | undefined = wanted ? list.find((t) => t.id === wanted) : list.find((t) => t.kind === 'main')
  return { env, threads, list, selected, wantedId: wanted }
}
