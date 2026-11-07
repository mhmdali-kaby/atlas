import { useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
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
import { DEFAULT_AUTONOMY, DEFAULT_PROFILE, enabledAutonomyLevels, isOnboarded, isPreset, usePatchSettings, useSettings, type RiskProfile } from './settings'
import { AutonomyStep, ConnectStep, RiskStep, WelcomeStep } from './steps'
import { BackfillStep } from './BackfillStep'
import { ClaudeStep, useClaudeReady } from './ClaudeStep'
import { TeamStep } from './TeamStep'
import { SourcesStep, type SourcesHandle } from './SourcesStep'
import './onboarding.css'


/**
 * /onboarding (design: js/pages/onboarding.js, css/auth.css "Onboarding frame"): a top bar,
 * the numbered stepper (Welcome → Connect OKX → Claude → Data sources → Risk profile → Autonomy →
 * Market history → Your team), the full-width step (title and lead on the start side, the form on the end side from
 * 1024 px) and Back / Skip or Set up later / Continue at the bottom. Finishing opens the War Room.
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
  const sourcesRef = useRef<SourcesHandle>(null)
  const [sourcesBusy, setSourcesBusy] = useState(false)
  // The slide direction: forward from the last step shown, or back to it (derived in render).
  const [shown, setShown] = useState({ index, direction: 1 })
  if (shown.index !== index) setShown({ index, direction: index > shown.index ? 1 : -1 })
  const direction = shown.index !== index ? (index > shown.index ? 1 : -1) : shown.direction

  const settings = useSettings('demo')
  const claude = useClaudeReady(step === 'claude')
  const doc = settings.data?.data
  const patch = usePatchSettings('demo')
  // The owner's picks; until they touch a card, the saved values (Balanced and level 2 on a new row).
  const [profilePick, setProfilePick] = useState<RiskProfile | null>(null)
  const [autonomyPick, setAutonomyPick] = useState<number | null>(null)
  const [askPick, setAskPick] = useState<string | null>(null)
  const profile: RiskProfile = profilePick ?? (isPreset(doc?.settings.risk_profile) ? doc.settings.risk_profile : DEFAULT_PROFILE)
  const enabledAutonomy = enabledAutonomyLevels(doc)
  const savedLevel = doc?.settings.autonomy_level
  const autonomy =
    autonomyPick ?? (savedLevel !== undefined && enabledAutonomy.includes(savedLevel) ? savedLevel : DEFAULT_AUTONOMY)
  const ask = askPick ?? String(doc?.settings.autonomy_ask_above_usd ?? '0')

  const session = useSession()
  const logout = useLogout()
  const client = useQueryClient()

  useEffect(() => {
    document.title = `${t('title')} · ${t(`steps.${step}`)}`
  }, [step, t])

  // Steps after Connect need a verified demo key; a URL that skips ahead goes back to Connect.
  if (index > 1 && !demo.isPending && !verified) return <Navigate to={onboardingPath('connect')} replace />
  // A finished setup does not reopen: /onboarding goes to the War Room (verified key + Finish saved).
  if (verified && isOnboarded(doc)) return <Navigate to={AFTER_ONBOARDING_PATH} replace />

  // Backfill progress never blocks: Continue is enabled at 0% (only the settings save gates it).
  const canContinue =
    step === 'connect'
      ? verified && !replacing
      : step === 'claude'
        ? verified && claude.ready
        : step === 'sources'
          ? verified && !sourcesBusy
          : index > 1
            ? verified && !!doc && !patch.isPending
            : true
  const busy = patch.isPending || sourcesBusy
  const last = index === STEPS.length - 1

  function go(next: StepId) {
    setMoved(true)
    scrollRef.current?.scrollTo?.({ top: 0 })
    setParams(next === 'welcome' ? {} : { step: next })
  }

  function saveFailed() {
    toast({ title: t('saveError.title'), body: t('saveError.body'), tone: 'error' })
  }

  function setUpLater() {
    // Carries on without Claude; the agents stay offline until the Agents page's check passes.
    go(STEPS[index + 1] as StepId)
    toast({ title: t('claude.laterToast'), body: t('claude.laterToastBody'), tone: 'info' })
  }

  function onContinue() {
    if (!canContinue) return
    if (step === 'sources') {
      // Saves the keys typed on the step; a refused key keeps the owner here, on its field.
      const save = sourcesRef.current?.save() ?? Promise.resolve(true)
      void save.then((ok) => ok && go('risk'))
      return
    }
    if (step === 'risk') {
      patch.mutate({ risk_profile: profile }, { onSuccess: () => go('autonomy'), onError: saveFailed })
      return
    }
    if (step === 'autonomy') {
      // The ask-above amount goes only with level 3, the one level that reads it.
      patch.mutate(
        { autonomy_level: autonomy, ...(autonomy === 3 ? { autonomy_ask_above_usd: ask } : {}) },
        { onSuccess: () => go(STEPS[index + 1] as StepId), onError: saveFailed },
      )
      return
    }
    if (last) {
      // Finish always writes onboarding_completed, so the version moves past 1 even with defaults.
      patch.mutate(
        { onboarding_completed: 1 },
        {
          onSuccess: () => {
            toast({ title: t('complete.title'), body: t('complete.body'), tone: 'success' })
            void navigate(AFTER_ONBOARDING_PATH, { replace: true })
          },
          onError: saveFailed,
        },
      )
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
              className="ob-col"
            >
              {step === 'welcome' && <WelcomeStep autoFocus={moved} />}
              {step === 'connect' && <ConnectStep autoFocus={moved} onReplacingChange={setReplacing} />}
              {step === 'claude' && <ClaudeStep autoFocus={moved} />}
              {step === 'sources' && <SourcesStep ref={sourcesRef} autoFocus={moved} onBusyChange={setSourcesBusy} />}
              {step === 'risk' && <RiskStep autoFocus={moved} value={profile} onChange={setProfilePick} />}
              {step === 'autonomy' && (
                <AutonomyStep
                  autoFocus={moved}
                  value={autonomy}
                  ask={ask}
                  onChange={(level, nextAsk) => {
                    setAutonomyPick(level)
                    setAskPick(nextAsk)
                  }}
                  enabled={enabledAutonomy}
                />
              )}
              {step === 'backfill' && <BackfillStep autoFocus={moved} />}
              {step === 'team' && <TeamStep autoFocus={moved} />}
            </motion.section>
          </AnimatePresence>
        </main>

        <div className="vibrancy-bar shadow-[0_-0.5px_0_var(--separator)]">
          <div className="flex items-center justify-between gap-3 px-(--page-gutter) py-3">
            {index > 0 ? (
              <Button size="lg" disabled={busy} onClick={() => go(STEPS[index - 1] as StepId)}>
                {t('back')}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex min-w-0 items-center gap-2 max-[600px]:gap-1">
              {step === 'claude' && !claude.ready && !claude.checking && (
                <Button size="lg" variant="plain" className="whitespace-nowrap max-[600px]:px-2.5" onClick={setUpLater} data-testid="ob-later">
                  {t('claude.later')}
                </Button>
              )}
              {step === 'sources' && !busy && (
                <Button size="lg" variant="plain" className="whitespace-nowrap max-[600px]:px-2.5" onClick={() => go('risk')} data-testid="ob-skip">
                  {t('skip')}
                </Button>
              )}
              <Button
                size="lg"
                variant="primary"
                className="min-w-[148px] max-[600px]:min-w-0"
                disabled={!canContinue && !busy}
                loading={busy}
                aria-describedby={step === 'connect' && !canContinue ? 'ob-continue-hint' : undefined}
                onClick={onContinue}
                data-testid="ob-next"
              >
                {busy ? t('saving') : last ? t('finish') : t('continue')}
              </Button>
            </div>
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
    <nav aria-label={t('progress')} className="px-(--page-gutter) pt-2 pb-1">
      <p className="sr-only" data-testid="ob-step-of">
        <StepOf n={current + 1} total={STEPS.length} />
      </p>
      {/* A phone shows "Step 3 of 6", the step's name and a six-part bar instead (design .ob-steps-sm). */}
      <div className="ob-steps-sm" aria-hidden="true">
        <p className="ob-sm-line">
          <span className="ob-sm-count">
            <StepOf n={current + 1} total={STEPS.length} />
          </span>
          <span className="ob-sm-name">{t(`steps.${STEPS[current] ?? 'welcome'}`)}</span>
        </p>
        <span className="ob-sm-bar">
          {STEPS.map((id, n) => (
            <i key={id} className={n < current ? 'is-done' : n === current ? 'is-current' : undefined} />
          ))}
        </span>
      </div>
      <ol className="ob-steps-ol flex items-center">
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
                  state !== 'current' && 'max-[1100px]:hidden',
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
                // Phones show only the current label: its step keeps its width and a 24 px line,
                // the other steps share the rest, so the label never runs into the next circle.
                state === 'current' && 'max-[1100px]:flex-none',
                state === 'current' && !isLast && 'max-[1100px]:after:w-6 max-[1100px]:after:flex-none',
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

/** "Step 3 of 6", with both figures as LTR islands. */
function StepOf({ n, total }: { n: number; total: number }) {
  const { t } = useTranslation('onboarding')
  return <Trans t={t} i18nKey="stepOf" values={{ n, total }} components={{ f: <bdi dir="ltr" className="fig" /> }} />
}
