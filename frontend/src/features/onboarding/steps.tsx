import { Clock, CloudOff, FlaskConical, Laptop, RefreshCw, ShieldCheck, SlidersHorizontal, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AppIcon, Button } from '../../ui'
import { ConnectionForm, KeyHelp, SavedKey, TestFailure, TestSuccess } from './ConnectionForm'
import { isVerified, useConnection, type TestOutcome } from './connections'
import { Notice } from './Notice'
import { AutonomyPicker, CompareDisclosure, LimitsCompare, RiskProfilePicker } from './Pickers'
import { useSettings, type RiskProfile } from './settings'

/** Every step: `autoFocus` moves focus to its heading when it opens from another step. */
export interface StepProps {
  autoFocus?: boolean
}

/** The step's heading: large title, focused (without a ring) when the step opens. */
export function StepHead({ title, lead, icon, autoFocus }: { title: string; lead: string; icon?: ReactNode; autoFocus?: boolean }) {
  const titleRef = useRef<HTMLHeadingElement>(null)
  // Only when the owner moved here from another step; a first visit starts at the page top.
  useEffect(() => {
    if (autoFocus) titleRef.current?.focus({ preventScroll: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the step opens
  }, [])
  return (
    <header className="grid justify-items-start gap-2.5">
      {icon}
      <h1 ref={titleRef} id="ob-step-title" tabIndex={-1} className="text-large-title text-label focus:outline-none">
        {title}
      </h1>
      <p className="max-w-[60ch] text-body text-label-secondary">{lead}</p>
    </header>
  )
}

/** Step 1 (design onboarding.js welcome()): what Atlas does, demo first, the risk statement. */
export function WelcomeStep({ autoFocus }: StepProps) {
  const { t } = useTranslation('onboarding')
  const facts = [
    { icon: FlaskConical, key: 'f1' },
    { icon: ShieldCheck, key: 'f2' },
    { icon: Laptop, key: 'f3' },
  ] as const
  return (
    <>
      <StepHead
        autoFocus={autoFocus}
        icon={<AppIcon size={56} className="mb-2" />}
        title={t('welcome.title')}
        lead={t('welcome.lead')}
      />
      <ul className="mt-1 grid gap-5.5">
        {facts.map(({ icon: Icon, key }) => (
          <li key={key} className="grid grid-cols-[44px_minmax(0,1fr)] items-start gap-4">
            <span className="grid size-11 place-items-center rounded-card bg-accent-wash text-accent">
              <Icon aria-hidden="true" strokeWidth={1.5} className="size-6" />
            </span>
            <div>
              <p className="text-headline font-semibold text-label">{t(`welcome.${key}.title`)}</p>
              <p className="mt-0.5 max-w-[56ch] text-subheadline text-label-secondary">{t(`welcome.${key}.body`)}</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="flex items-center gap-2 text-footnote text-label-secondary">
        <Clock aria-hidden="true" strokeWidth={1.5} className="size-3.5" />
        <span>{t('welcome.time')}</span>
      </p>
      <div
        data-testid="welcome-risk"
        className="mt-0.5 flex items-center gap-3 rounded-card bg-fill-4 px-4 py-3.5 shadow-[inset_0_0_0_0.5px_var(--hairline-color)]"
      >
        <TriangleAlert aria-hidden="true" strokeWidth={1.5} className="size-5 shrink-0 text-warning-ink" />
        <p className="text-subheadline font-medium text-label">{t('welcome.risk')}</p>
      </div>
    </>
  )
}

/**
 * Step 2 (design onboarding.js connect()): the ConnectionForm, then the test result. A saved,
 * verified key shows only as "•••• last4" with the account rows; Replace keys reopens the form.
 */
export function ConnectStep({
  autoFocus,
  onReplacingChange,
}: StepProps & {
  /** Replace keys reopens the form; Continue stays locked until the new keys pass. */
  onReplacingChange?: (replacing: boolean) => void
}) {
  const { t } = useTranslation('onboarding')
  const query = useConnection('demo')
  const { connection } = query
  const [replacing, setReplacingState] = useState(false)
  const setReplacing = (next: boolean) => {
    setReplacingState(next)
    onReplacingChange?.(next)
  }
  const [outcome, setOutcome] = useState<TestOutcome | null>(null)
  const resultTitleRef = useRef<HTMLParagraphElement>(null)
  const errorRef = useRef<HTMLDivElement>(null)

  // Move focus to the result once a test lands, so it is read and seen without scrolling.
  useEffect(() => {
    if (!outcome) return
    if (outcome.status === 'ok') resultTitleRef.current?.focus()
    else errorRef.current?.focus()
  }, [outcome])

  const head = <StepHead autoFocus={autoFocus} title={t('connect.title')} lead={t('connect.lead')} />

  if (query.isPending) {
    return (
      <>
        {head}
        <div aria-busy="true" aria-label={t('checking')} className="grid gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="grid gap-1.5">
              <span className="h-4 w-28 animate-pulse rounded-[6px] bg-fill-3" />
              <span className="h-10 w-full animate-pulse rounded-control bg-fill-4" />
            </div>
          ))}
        </div>
      </>
    )
  }

  if (query.isError) {
    return (
      <>
        {head}
        <Notice
          tone="error"
          role="alert"
          icon={CloudOff}
          title={t('connect.loadError.title')}
          actions={
            <Button size="sm" icon={<RefreshCw />} onClick={() => void query.refetch()}>
              {t('retry')}
            </Button>
          }
        >
          {t('connect.loadError.body')}
        </Notice>
      </>
    )
  }

  const okOutcome = outcome?.status === 'ok' ? outcome : null
  if (isVerified(connection) && !replacing) {
    return (
      <>
        {head}
        <SavedKey
          connection={connection}
          onReplace={() => {
            setOutcome(null)
            setReplacing(true)
          }}
        />
        <TestSuccess
          connection={connection}
          acctLv={okOutcome?.acctLv}
          warnings={okOutcome?.warnings}
          titleRef={resultTitleRef}
        />
      </>
    )
  }

  // A failed test replaces the help, right under the title. A stored failure from an earlier
  // visit shows too, so reopening the step says why the key is not verified.
  const failure =
    outcome?.status === 'failed'
      ? outcome
      : !outcome && connection?.last_test_error
        ? { error: connection.last_test_error, okxCode: null }
        : null

  return (
    <>
      {head}
      {failure ? <TestFailure ref={errorRef} error={failure.error} okxCode={failure.okxCode} /> : <KeyHelp />}
      <ConnectionForm
        connection={connection}
        idPrefix="ob"
        hint={t('connect.continueHint')}
        onTestStart={() => setOutcome(null)}
        onResult={(result) => {
          if (result.status === 'ok') setReplacing(false)
          setOutcome(result)
        }}
      />
    </>
  )
}

/**
 * Step 3 (design onboarding.js risk()): the three profile cards from GET /api/settings presets,
 * then "Compare all ten limits". The choice is saved when the owner presses Continue.
 */
export function RiskStep({
  autoFocus,
  value,
  onChange,
}: StepProps & { value: RiskProfile; onChange: (profile: RiskProfile) => void }) {
  const { t } = useTranslation('onboarding')
  const query = useSettings('demo')
  const head = <StepHead autoFocus={autoFocus} title={t('risk.title')} lead={t('risk.lead')} />

  if (query.isPending) {
    return (
      <>
        {head}
        <div aria-busy="true" aria-label={t('risk.loading')} className="ob-profile-cards" data-testid="risk-loading">
          {[0, 1, 2].map((i) => (
            <div key={i} className="ob-choice grid gap-2" aria-hidden="true">
              <span className="ob-skeleton h-5 w-3/5 animate-pulse" />
              <span className="ob-skeleton h-3.5 w-[90%] animate-pulse" />
              <span className="ob-skeleton mt-2 h-24 w-full animate-pulse" />
            </div>
          ))}
        </div>
      </>
    )
  }

  if (query.isError) {
    return (
      <>
        {head}
        <Notice
          tone="error"
          role="alert"
          icon={CloudOff}
          title={t('risk.error.title')}
          actions={
            <Button size="sm" icon={<RefreshCw />} onClick={() => void query.refetch()}>
              {t('retry')}
            </Button>
          }
        >
          {t('risk.error.body')}
        </Notice>
      </>
    )
  }

  const presets = query.data.data.presets
  return (
    <>
      {head}
      <RiskProfilePicker value={value} onChange={onChange} presets={presets} />
      <CompareDisclosure>
        <LimitsCompare presets={presets} selected={value} />
      </CompareDisclosure>
    </>
  )
}

/** Step 4 (design onboarding.js autonomy()): levels 1–4; Phase 0 allows only 1 and 2. */
export function AutonomyStep({ autoFocus, value, onChange }: StepProps & { value: number; onChange: (level: number) => void }) {
  const { t } = useTranslation('onboarding')
  return (
    <>
      <StepHead autoFocus={autoFocus} title={t('autonomy.title')} lead={t('autonomy.lead')} />
      <AutonomyPicker value={value} onChange={onChange} />
      <p className="flex items-center gap-2 text-footnote text-label-secondary">
        <SlidersHorizontal aria-hidden="true" strokeWidth={1.5} className="size-3.5" />
        <span>{t('autonomy.note')}</span>
      </p>
    </>
  )
}
