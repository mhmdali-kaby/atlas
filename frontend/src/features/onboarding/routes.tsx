import type { FeatureModule } from '../../app/feature'
import { RequireAuth } from '../auth/RequireAuth'
import { OnboardingGate } from './OnboardingGate'
import { OnboardingPage } from './OnboardingPage'

const onboarding: FeatureModule = {
  // The shell's index route: "/" sends an owner with no verified demo connection to /onboarding.
  routes: [{ index: true, element: <OnboardingGate /> }],
  // The wizard has its own frame (no sidebar), so it sits outside the shell, behind the session guard.
  publicRoutes: [
    {
      path: '/onboarding',
      element: (
        <RequireAuth>
          <OnboardingPage />
        </RequireAuth>
      ),
    },
  ],
}

export default onboarding
