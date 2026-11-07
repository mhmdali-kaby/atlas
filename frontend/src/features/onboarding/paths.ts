export const STEPS = ['welcome', 'connect', 'claude', 'sources', 'risk', 'autonomy', 'backfill', 'team'] as const
export type StepId = (typeof STEPS)[number]

/** Where Finish ("Open the War Room") lands, after onboarding_completed is saved (ATL-234). */
export const AFTER_ONBOARDING_PATH = '/warroom'

export function onboardingPath(step: StepId = 'welcome'): string {
  return step === 'welcome' ? '/onboarding' : `/onboarding?step=${step}`
}

export function parseStep(value: string | null): StepId {
  return (STEPS as readonly string[]).includes(value ?? '') ? (value as StepId) : 'welcome'
}
