export const STEPS = ['welcome', 'connect', 'risk', 'autonomy'] as const
export type StepId = (typeof STEPS)[number]

/** Where Finish setup lands (ATL-19 saves the choices first). */
export const AFTER_ONBOARDING_PATH = '/portfolio'

export function onboardingPath(step: StepId = 'welcome'): string {
  return step === 'welcome' ? '/onboarding' : `/onboarding?step=${step}`
}

export function parseStep(value: string | null): StepId {
  return (STEPS as readonly string[]).includes(value ?? '') ? (value as StepId) : 'welcome'
}
