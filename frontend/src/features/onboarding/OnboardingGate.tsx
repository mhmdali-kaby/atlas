import { useTranslation } from 'react-i18next'
import { Navigate } from 'react-router'
import { HomeRedirect } from '../../app/AppShell'
import type { NavEntry } from '../../app/feature'
import { Spinner } from '../../ui'
import { isVerified, useConnection } from './connections'
import { onboardingPath } from './paths'
import { isOnboarded, useSettings } from './settings'

/**
 * The signed-in landing ("/", where sign-in sends the owner): with no verified OKX demo
 * connection it opens /onboarding at Welcome; with a verified key but setup not finished, at the
 * risk step; otherwise the first sidebar page (Portfolio).
 * Registered by this feature as the shell's index route, so router.tsx and AppShell.tsx
 * stay untouched (a feature's index route wins over the shell's fallback).
 */
export function OnboardingGate({ nav }: { nav?: readonly NavEntry[] }) {
  const { t } = useTranslation('onboarding')
  const demo = useConnection('demo')
  const verified = isVerified(demo.connection)
  const settings = useSettings('demo', verified)

  if (demo.isPending || (verified && settings.isPending)) {
    return (
      <div role="status" aria-label={t('checking')} className="grid place-items-center pt-24 text-label-secondary">
        <Spinner size={28} />
      </div>
    )
  }
  // A failed lookup must not trap the owner in the wizard; the pages show their own errors.
  if (!demo.isError && !verified) return <Navigate to={onboardingPath('welcome')} replace />
  if (verified && !settings.isError && !isOnboarded(settings.data?.data)) return <Navigate to={onboardingPath('risk')} replace />
  return <HomeRedirect nav={nav} />
}
