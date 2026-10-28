import { useTranslation } from 'react-i18next'
import { Navigate } from 'react-router'
import { HomeRedirect } from '../../app/AppShell'
import type { NavEntry } from '../../app/feature'
import { Spinner } from '../../ui'
import { isVerified, useConnection } from './connections'
import { onboardingPath } from './paths'

/**
 * The signed-in landing ("/", where sign-in sends the owner): with no verified OKX demo
 * connection it opens /onboarding at Welcome; otherwise the first sidebar page, as before.
 * Registered by this feature as the shell's index route, so router.tsx and AppShell.tsx
 * stay untouched (a feature's index route wins over the shell's fallback).
 */
export function OnboardingGate({ nav }: { nav?: readonly NavEntry[] }) {
  const { t } = useTranslation('onboarding')
  const demo = useConnection('demo')

  if (demo.isPending) {
    return (
      <div role="status" aria-label={t('checking')} className="grid place-items-center pt-24 text-label-secondary">
        <Spinner size={28} />
      </div>
    )
  }
  // A failed lookup must not trap the owner in the wizard; the pages show their own errors.
  if (!demo.isError && !isVerified(demo.connection)) return <Navigate to={onboardingPath('welcome')} replace />
  return <HomeRedirect nav={nav} />
}
