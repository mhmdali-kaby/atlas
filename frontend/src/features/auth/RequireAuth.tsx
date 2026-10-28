import { useCallback, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation } from 'react-router'
import { Spinner } from '../../ui'
import { IDLE_LOCK_MS, loginPath, useAuthExpiredListener, useLogout, useSession } from './session'
import { useIdleLock } from './useIdleLock'

/**
 * Guards every page inside the shell: no session → /login with the requested page
 * in `next`; 15 minutes without input → sign out and /login?reason=idle.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation('auth')
  useAuthExpiredListener()
  const session = useSession()
  const location = useLocation()
  const logout = useLogout()
  const [locked, setLocked] = useState(false)
  const signedIn = session.data !== null && session.data !== undefined

  const lock = useCallback(() => {
    setLocked(true)
    logout.mutate()
  }, [logout])
  useIdleLock(signedIn && !locked, IDLE_LOCK_MS, lock)

  const requested = `${location.pathname}${location.search}${location.hash}`

  if (locked || session.data === null || (session.data === undefined && session.isError)) {
    // While the idle sign-out request is in flight, keep the redirect until it lands.
    if (locked && logout.isPending) return <Checking label={t('checking')} />
    return <Navigate to={loginPath(requested, locked ? 'idle' : undefined)} replace />
  }
  if (!signedIn) return <Checking label={t('checking')} />
  return <>{children}</>
}

function Checking({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className="grid min-h-dvh place-items-center text-label-secondary">
      <Spinner size={28} />
    </div>
  )
}
