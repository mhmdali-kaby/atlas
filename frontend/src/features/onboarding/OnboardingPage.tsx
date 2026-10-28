import { useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { EnvBadge } from '../../app/shell/EnvBadge'
import { AccountMenu, LanguageButton, ThemeMenu, ToolbarGroup } from '../../app/shell/Prefs'
import { RiskFooter } from '../../app/shell/RiskFooter'
import { AppIcon, Button, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { FADE } from '../../ui/motion'
import { SIGNED_OUT_KEY, useLogout, useSession } from '../auth/session'
import { isVerified, useConnection } from './connections'
import { AFTER_ONBOARDING_PATH, onboardingPath, parseStep, STEPS, type StepId } from './paths'
import { ConnectStep, PendingStep, WelcomeStep } from './steps'


/**
 * /onboarding (design: js/pages/onboarding.js, css/auth.css "Onboarding frame"): a top bar,
 * the numbered four-step stepper, a centred 640px column and Back / Continue at the bottom.
 * The step is in the URL (?step=connect), so reload and the browser's Back keep it.
 */
export function OnboardingPage() {
  const { t } = useTranslation(['onboarding', 'shell'])
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const step = parseStep(params.get('step'))
  const index = STEPS.indexOf(step)
  const demo = useConnection('demo')
  const verified = isVerified(demo.connection)
  const [replacing, setReplacing] = useState(false)
  const [moved, setMoved] = useState(false)
  const scrollRef = useRef<HTMLElement>(null)
  // The slide direction: forward from the last step shown, or back to it (derived in render).
  const [shown, setShown] = useState({ index, direction: 1 })
  if (shown.index !== index) setShown({ index, direction: index > shown.index ? 1 : -1 })
  const direction = shown.index !== index ? (index > shown.index ? 1 : -1) : shown.direction

  const session = useSession()
  const logout = useLogout()
  const client = useQueryClient()

  useEffect(() => {
    document.title = `${t('title')} · ${t(`steps.${step}`)}`
  }, [step, t])

  // Steps after Connect need a verified demo key; a URL that skips ahead goes back to Connect.
  if (index > 1 && !demo.isPending && !verified) return <Navigate to={onboardingPath('connect')} replace />

  const canContinue = step === 'connect' ? verified && !replacing : index > 1 ? verified : true
  const last = index === STEPS.length - 1

  function go(next: StepId) {
    setMoved(true)
    scrollRef.current?.scrollTo?.({ top: 0 })
    setParams(next === 'welcome' ? {} : { step: next })
  }

  function onContinue() {
    if (!canContinue) return
    if (last) {
      void navigate(AFTER_ONBOARDING_PATH)
      return
    }
    go(STEPS[index + 1] as StepId)
  }

  function signOut() {
    client.setQueryData(SIGNED_OUT_KEY, true)
    logout.mutate()
    toast({ title: t('shell:signedOut'), tone: 'info' })
  }

  const user = session.data

  return (
    <MotionConfig reducedMotion="user">
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-1 bg-background bg-[radial-gradient(60%_40%_at_50%_0%,var(--wash-1),transparent_70%)]"
      />
      <div className="fixed inset-x-0 top-0 bottom-[var(--footer-height)] grid grid-rows-[var(--toolbar-height)_auto_minmax(0,1fr)_auto]">
        <header className="flex items-center gap-2.5 px-4">
          <AppIcon size={28} />
          <span className="text-headline font-semibold text-label">{t('title')}</span>
          <span className="flex-1" />
          <span className="max-[760px]:hidden">
            <EnvBadge />
          </span>
          <ToolbarGroup>
            <LanguageButton />
            <ThemeMenu />
            {user && <AccountMenu name={user.name} email={user.email} onSignOut={signOut} />}
          </ToolbarGroup>
        </header>

        <Stepper current={index} onGo={go} />

        <main ref={scrollRef} aria-labelledby="ob-step-title" className="overflow-x-hidden overflow-y-auto">
          <AnimatePresence mode="wait" initial={false}>
            <motion.section
              key={step}
              data-step={step}
              initial={{ opacity: 0, x: 16 * direction * (document.documentElement.dir === 'rtl' ? -1 : 1) }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={FADE}
              className="mx-auto grid max-w-[680px] content-start gap-5.5 px-6 pt-7 pb-14 max-[760px]:pt-6"
            >
              {step === 'welcome' && <WelcomeStep autoFocus={moved} />}
              {step === 'connect' && <ConnectStep autoFocus={moved} onReplacingChange={setReplacing} />}
              {(step === 'risk' || step === 'autonomy') && <PendingStep step={step} autoFocus={moved} />}
            </motion.section>
          </AnimatePresence>
        </main>

        <div className="vibrancy-bar shadow-[0_-0.5px_0_var(--separator)]">
          <div className="mx-auto flex max-w-[680px] items-center justify-between gap-3 px-6 py-3">
            {index > 0 ? (
              <Button size="lg" onClick={() => go(STEPS[index - 1] as StepId)}>
                {t('back')}
              </Button>
            ) : (
              <span />
            )}
            <Button
              size="lg"
              variant="primary"
              className="min-w-[148px]"
              disabled={!canContinue}
              aria-describedby={step === 'connect' && !canContinue ? 'ob-continue-hint' : undefined}
              onClick={onContinue}
              data-testid="ob-next"
            >
              {last ? t('finish') : t('continue')}
            </Button>
            {step === 'connect' && !canContinue && (
              <span id="ob-continue-hint" className="sr-only">
                {t('connect.continueHint')}
              </span>
            )}
          </div>
        </div>
      </div>
      <RiskFooter />
    </MotionConfig>
  )
}

/** Design .ob-steps: a numbered sequence; finished steps are buttons back to themselves. */
function Stepper({ current, onGo }: { current: number; onGo: (step: StepId) => void }) {
  const { t } = useTranslation('onboarding')
  return (
    <nav aria-label={t('progress')} className="px-6 pt-2 pb-1">
      <p className="sr-only">{t('stepOf', { n: current + 1 })}</p>
      <ol className="mx-auto flex max-w-[680px] items-center">
        {STEPS.map((id, n) => {
          const state = n < current ? 'done' : n === current ? 'current' : 'next'
          const isLast = n === STEPS.length - 1
          const inner = (
            <>
              <span
                aria-hidden="true"
                className={cx(
                  'grid size-[26px] shrink-0 place-items-center rounded-full text-footnote font-semibold transition-colors duration-[var(--dur-base)]',
                  state === 'current' && 'bg-accent-solid text-on-solid shadow-[0_2px_8px_color-mix(in_srgb,var(--accent)_35%,transparent)]',
                  state === 'done' && 'bg-accent-wash text-accent-ink',
                  state === 'next' && 'text-label-secondary shadow-[inset_0_0_0_1.5px_var(--separator-strong)]',
                )}
              >
                {state === 'done' ? <Check strokeWidth={2.5} className="size-3.5" /> : <span className="fig">{n + 1}</span>}
              </span>
              <span
                className={cx(
                  'text-subheadline',
                  state === 'current' ? 'font-semibold text-label' : state === 'done' ? 'text-label' : 'text-label-secondary',
                  state !== 'current' && 'max-[760px]:hidden',
                )}
              >
                {t(`steps.${id}`)}
              </span>
            </>
          )
          return (
            <li
              key={id}
              data-state={state}
              className={cx(
                'flex min-w-0 items-center',
                isLast ? 'flex-none' : 'flex-1',
                !isLast &&
                  'after:mx-2.5 after:h-[1.5px] after:min-w-4 after:flex-1 after:rounded-[1px] after:content-[""] ' +
                    (state === 'done' ? 'after:bg-accent' : 'after:bg-separator'),
              )}
            >
              {state === 'done' ? (
                <button
                  type="button"
                  onClick={() => onGo(id)}
                  className="-m-1.5 inline-flex items-center gap-2 rounded-full p-1.5 whitespace-nowrap hover:bg-fill-4 focus-visible:shadow-focus focus-visible:outline-none"
                >
                  <span className="sr-only">{t('done')}: </span>
                  {inner}
                </button>
              ) : (
                <span aria-current={state === 'current' ? 'step' : undefined} className="-m-1.5 inline-flex items-center gap-2 p-1.5 whitespace-nowrap">
                  {inner}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
