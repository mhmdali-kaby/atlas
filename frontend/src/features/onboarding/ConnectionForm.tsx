import { Check, CircleAlert, KeyRound, Lock, Plug, Replace, ServerOff, ShieldAlert, ShieldCheck, TriangleAlert } from 'lucide-react'
import { motion } from 'motion/react'
import { useRef, useState, type FormEvent, type ReactNode, type Ref } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Button, Seal, SecureField, TextField } from '../../ui'
import { SPRING } from '../../ui/motion'
import {
  canWithdraw,
  ConnectionValidationError,
  useSaveAndTest,
  type Connection,
  type ConnectionValues,
  type TestOutcome,
} from './connections'
import { Notice } from './Notice'

type FieldErrors = Partial<Record<keyof ConnectionValues, string>>

export interface ConnectionFormProps {
  /** The stored connection to replace (PATCH), or null to create one (POST). */
  connection: Connection | null
  env?: Connection['env']
  /** Called after every Test connection press, with what it ended in. */
  onResult?: (outcome: TestOutcome) => void
  /** Called when the save-and-test starts, so the caller can clear an old result. */
  onTestStart?: () => void
  /** A line next to the Test connection button (onboarding: "Continue unlocks after…"). */
  hint?: ReactNode
  /** Prefix for field ids, so two forms on one page never share one. */
  idPrefix?: string
}

/**
 * Design forms.js ConnectionForm: label, API key, secret key and passphrase (masked), then
 * Test connection, which saves the keys (POST or PATCH /api/connections) and tests them
 * (POST /api/connections/{id}/test). The typed secrets stay in this component only; they are
 * cleared once a test succeeds. Shared with Settings → Connections (ATL-20).
 */
export function ConnectionForm({ connection, env = 'demo', onResult, onTestStart, hint, idPrefix = 'cf' }: ConnectionFormProps) {
  const { t } = useTranslation('onboarding')
  const saveAndTest = useSaveAndTest(env)
  const [values, setValues] = useState<ConnectionValues>({
    label: connection?.label ?? t('cf.defaultLabel'),
    apiKey: '',
    secret: '',
    passphrase: '',
  })
  const [errors, setErrors] = useState<FieldErrors>({})
  const labelRef = useRef<HTMLInputElement>(null)
  const apiKeyRef = useRef<HTMLInputElement>(null)
  const secretRef = useRef<HTMLInputElement>(null)
  const passphraseRef = useRef<HTMLInputElement>(null)
  const testing = saveAndTest.isPending

  const set = (field: keyof ConnectionValues) => (e: { currentTarget: HTMLInputElement }) => {
    const value = e.currentTarget.value
    setValues((v) => ({ ...v, [field]: value }))
  }

  function focusFirst(found: FieldErrors) {
    const first = (['label', 'apiKey', 'secret', 'passphrase'] as const).find((f) => found[f])
    const target = { label: labelRef, apiKey: apiKeyRef, secret: secretRef, passphrase: passphraseRef }
    if (first) target[first].current?.focus()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (testing) return
    const found: FieldErrors = {}
    const label = values.label.trim() || t('cf.defaultLabel')
    if (!values.apiKey.trim()) found.apiKey = t('cf.required.apiKey')
    if (!values.secret.trim()) found.secret = t('cf.required.secret')
    if (!values.passphrase.trim()) found.passphrase = t('cf.required.passphrase')
    setErrors(found)
    if (Object.keys(found).length > 0) {
      focusFirst(found)
      return
    }
    onTestStart?.()
    saveAndTest.mutate(
      { existing: connection, values: { ...values, label } },
      {
        onSuccess: (outcome) => {
          if (outcome.status === 'ok') setValues((v) => ({ ...v, apiKey: '', secret: '', passphrase: '' }))
          onResult?.(outcome)
        },
        onError: (err) => {
          if (err instanceof ConnectionValidationError) {
            const fieldErrors: FieldErrors = {}
            for (const f of Object.keys(err.fields) as (keyof ConnectionValues)[]) fieldErrors[f] = t('cf.invalid')
            setErrors(fieldErrors)
            focusFirst(fieldErrors)
          } else {
            onResult?.({ status: 'failed', error: 'unknown', okxCode: null })
          }
        },
      },
    )
  }

  return (
    <form className="grid gap-5" noValidate onSubmit={submit} aria-busy={testing || undefined}>
      <div className="grid gap-4">
        <TextField
          ref={labelRef}
          id={`${idPrefix}-label`}
          label={t('cf.label')}
          hint={t('cf.labelHint')}
          value={values.label}
          onChange={set('label')}
          maxLength={100}
          disabled={testing}
          autoComplete="off"
          error={errors.label}
        />
        <TextField
          ref={apiKeyRef}
          id={`${idPrefix}-key`}
          label={t('cf.apiKey')}
          value={values.apiKey}
          onChange={set('apiKey')}
          placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
          dir="ltr"
          inputClassName="font-mono text-subheadline tracking-[0.02em]"
          autoComplete="off"
          spellCheck={false}
          autoCapitalize="off"
          maxLength={255}
          disabled={testing}
          error={errors.apiKey}
        />
        <SecureField
          ref={secretRef}
          id={`${idPrefix}-secret`}
          label={t('cf.secret')}
          value={values.secret}
          onChange={set('secret')}
          maxLength={255}
          disabled={testing}
          error={errors.secret}
        />
        <SecureField
          ref={passphraseRef}
          id={`${idPrefix}-pass`}
          label={t('cf.passphrase')}
          hint={t('cf.passphraseHint')}
          value={values.passphrase}
          onChange={set('passphrase')}
          maxLength={255}
          disabled={testing}
          error={errors.passphrase}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3.5">
        <Button type="submit" variant="primary" icon={<Plug />} loading={testing}>
          {testing ? t('cf.testing') : t('cf.test')}
        </Button>
        {hint !== undefined && (
          <span className="inline-flex items-center gap-1.5 text-footnote text-label-secondary">
            <Lock aria-hidden="true" strokeWidth={1.5} className="size-3.5 shrink-0" />
            <span>{hint}</span>
          </span>
        )}
      </div>
      <p className="sr-only" aria-live="polite">
        {testing ? t('tr.testing') : ''}
      </p>
    </form>
  )
}

/** The info notice above an empty form: how to create a Read + Trade demo key. */
export function KeyHelp() {
  const { t } = useTranslation('onboarding')
  return (
    <Notice tone="info" icon={KeyRound} title={t('cf.where.title')}>
      {t('cf.where.body')}
    </Notice>
  )
}

/** A stored key: "Key saved · API key •••• 7Q2F", never the key itself, with Replace keys. */
export function SavedKey({ connection, onReplace }: { connection: Connection; onReplace?: () => void }) {
  const { t } = useTranslation('onboarding')
  return (
    <div
      data-testid="saved-key"
      className="flex items-center gap-3 rounded-card bg-background-grouped-row px-3.5 py-3 shadow-[0_0_0_0.5px_var(--hairline-color)]"
    >
      <span className="grid size-7 shrink-0 place-items-center rounded-[7px] bg-accent-wash text-accent">
        <KeyRound aria-hidden="true" strokeWidth={1.5} className="size-4" />
      </span>
      <div className="grid min-w-0 flex-1">
        <span className="text-callout font-semibold text-label">{t('cf.saved')}</span>
        <span className="text-footnote text-label-secondary">
          <Trans
            t={t}
            i18nKey="cf.savedMeta"
            values={{ last4: connection.api_key_last4 }}
            components={{ fig: <bdi dir="ltr" className="fig" /> }}
          />
        </span>
      </div>
      {onReplace && (
        <Button size="sm" icon={<Replace />} onClick={onReplace}>
          {t('cf.replace')}
        </Button>
      )}
    </div>
  )
}

/** Design forms.js accountRows: mode, position mode, permissions and UID tail of a tested key. */
export function AccountRows({ connection, accountMode }: { connection: Connection; accountMode?: string | null }) {
  const { t } = useTranslation('onboarding')
  const mode = accountMode ?? connection.account_mode
  const perms = connection.permissions ?? []
  const withdraw = perms.includes('withdraw')
  const rows: [string, ReactNode][] = [
    [
      t('acct.mode'),
      mode === 'spot' ? (
        <span key="mode" className="inline-flex items-center gap-1.5 font-medium text-warning-ink">
          <TriangleAlert aria-hidden="true" strokeWidth={1.5} className="size-3.5" />
          {t('mode.spot')}
        </span>
      ) : mode ? (
        t(`mode.${mode}`, { defaultValue: t('mode.unknown') })
      ) : (
        '—'
      ),
    ],
    [t('acct.posMode'), connection.position_mode ? t(`pos.${connection.position_mode}`, { defaultValue: connection.position_mode }) : '—'],
    [
      t('acct.perms'),
      <span key="perms" className="inline-flex flex-wrap justify-end gap-1.5">
        {perms
          .filter((p) => p !== 'withdraw')
          .map((p) => (
            <Badge key={p} tone="quiet" icon={<Check />}>
              {t(`perm.${p}`, { defaultValue: p })}
            </Badge>
          ))}
        {withdraw ? (
          <Badge tone="warning" icon={<ShieldAlert />}>
            {t('perm.withdraw')}
          </Badge>
        ) : (
          <Badge tone="profit" icon={<ShieldCheck />}>
            {t('perm.withdrawOff')}
          </Badge>
        )}
      </span>,
    ],
    [
      t('acct.uid'),
      connection.uid_last4 ? (
        <bdi key="uid" dir="ltr" className="fig" data-testid="uid-tail">
          •••• {connection.uid_last4}
        </bdi>
      ) : (
        '—'
      ),
    ],
  ]
  return (
    <dl className="grid rounded-card bg-background-secondary">
      {rows.map(([label, value], i) => (
        <div
          key={label}
          className={
            'flex min-h-10 items-center justify-between gap-4 px-4 py-2 text-subheadline ' +
            (i > 0 ? 'shadow-[inset_0_0.5px_0_var(--separator)]' : '')
          }
        >
          <dt className="text-label">{label}</dt>
          <dd className="text-end font-semibold text-label">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Badge({ tone, icon, children }: { tone: 'quiet' | 'profit' | 'warning'; icon: ReactNode; children: ReactNode }) {
  const cls = {
    quiet: 'bg-transparent shadow-[inset_0_0_0_1px_var(--separator)] text-label-secondary',
    profit: 'bg-profit-wash text-profit-ink',
    warning: 'bg-warning-wash text-warning-ink',
  }[tone]
  return (
    <span className={`inline-flex h-[22px] items-center gap-[5px] rounded-full px-2 text-footnote font-semibold whitespace-nowrap ${cls} [&_svg]:size-[13px] [&_svg]:stroke-2`}>
      <span aria-hidden="true" className="contents">
        {icon}
      </span>
      {children}
    </span>
  )
}

/**
 * The orange notice for a demo key with Withdraw (decision 240). Shown while the stored
 * connection's permissions include withdraw, so it stays after a reload; never a toast.
 */
export function WithdrawWarning() {
  const { t } = useTranslation('onboarding')
  return (
    <Notice tone="warning" icon={ShieldAlert} title={t('tr.withdrawWarning.title')} data-testid="withdraw-warning">
      {t('tr.withdrawWarning.body')}
    </Notice>
  )
}

/** A successful test: the account seal stamps in, then the account rows. */
export function TestSuccess({
  connection,
  acctLv,
  warnings = [],
  titleRef,
}: {
  connection: Connection
  acctLv?: string | null
  warnings?: readonly string[]
  titleRef?: Ref<HTMLParagraphElement>
}) {
  const { t } = useTranslation('onboarding')
  const spot = acctLv === '1' || connection.account_mode === 'spot'
  const last4 = connection.uid_last4 ?? '----'
  return (
    <motion.div
      role="status"
      data-testid="test-success"
      initial={{ opacity: 0, y: -6, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={SPRING}
      className="grid gap-3.5 rounded-panel bg-background-grouped-row p-4 shadow-[0_0_0_0.5px_var(--hairline-color),var(--ui-shadow-2)]"
    >
      <div className="flex items-center gap-4">
        <span role="img" aria-label={t('tr.sealLabel', { last4 })} className="relative grid shrink-0 place-items-center">
          <Seal seed={`okx:${connection.env}:${last4}`} size={88} />
          <Check aria-hidden="true" strokeWidth={2.5} className="absolute size-4 text-profit-ink" />
        </span>
        <div>
          <p ref={titleRef} tabIndex={-1} className="text-headline font-semibold text-label focus:outline-none">
            {t('tr.success.title')}
          </p>
          <p className="mt-0.5 text-footnote text-label-secondary">{t('tr.success.body')}</p>
        </div>
      </div>
      <AccountRows connection={connection} accountMode={spot ? 'spot' : undefined} />
      {spot && (
        <Notice tone="warning" icon={TriangleAlert} title={t('tr.spot.title')}>
          {t('tr.spot.body')}
        </Notice>
      )}
      {canWithdraw(connection, warnings) && <WithdrawWarning />}
    </motion.div>
  )
}

/** A failed test or save: the mapped error, read out as an alert. */
export function TestFailure({ error, okxCode, ref }: { error: string; okxCode?: string | null; ref?: Ref<HTMLDivElement> }) {
  const { t, i18n } = useTranslation('onboarding')
  const key = i18n.exists(`onboarding:tr.errors.${error}.title`) ? error : 'unknown'
  const icon = key === 'withdraw_permission_enabled' ? ShieldAlert : key === 'trading_core_unavailable' ? ServerOff : CircleAlert
  return (
    <Notice
      ref={ref}
      tone="error"
      role="alert"
      tabIndex={-1}
      icon={icon}
      title={t(`tr.errors.${key}.title`)}
      data-testid="test-error"
    >
      {t(`tr.errors.${key}.body`, { code: okxCode ?? '—' })}
    </Notice>
  )
}
