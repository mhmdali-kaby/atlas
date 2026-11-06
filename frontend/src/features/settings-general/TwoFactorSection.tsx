import { useQueryClient } from '@tanstack/react-query'
import {
  Check,
  CircleAlert,
  CircleCheck,
  CloudOff,
  Copy,
  Download,
  KeyRound,
  RadioTower,
  RefreshCw,
  Shield,
  ShieldCheck,
  ShieldX,
  TriangleAlert,
} from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { ApiError, api, apiFetch, useApiQuery } from '../../lib/api'
import type { Lang } from '../../lib/format'
import { AppIcon, Button, OTP_LENGTH, OtpDial, OtpField, SecureField, Seal, Sheet, Skeleton, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { formatDate, formatTime } from '../warroom/time'
import './twofactor.css'

/*
 * Settings → General → Sign-in (ATL-247; decision 265 (1); design v22 §5.2–5.3) on ATL-245's
 * /api/me/two-factor. Off: one row with Turn on…. On: the date, eight marks for the recovery
 * codes left, New codes… and Turn off…, each behind the password. Turning on is one sheet in
 * three steps (password → scan + code → codes); the codes step ignores Esc and clicks outside,
 * because the codes are never shown again, so only "I saved them" closes it.
 */

const NS = 'settings-general'
const TWO_FACTOR_KEY = ['me', 'two-factor'] as const
const TOTAL_CODES = 8
const LOW_CODES = 2

export interface TwoFactorStatus {
  enabled: boolean
  confirmed_at: string | null
  recovery_codes_left: number
}
interface CodesResponse {
  data: TwoFactorStatus & { recovery_codes: string[] }
}

type Kind = 'on' | 'off' | 'regen'
type Flow =
  | { kind: Kind; step: 'password' }
  | { kind: 'on'; step: 'scan'; qrSvg: string; secret: string }
  | { kind: 'on' | 'regen'; step: 'codes'; codes: string[]; madeAt: number }

const keyGroups = (k: string) => (k.match(/.{1,4}/g) ?? []).join(' ')

/** The API's message for a field, else ours; 429 and no answer get their own words. */
function errorText(err: unknown, field: string, t: (k: string) => string, fallback: string): { field?: string; alert?: string } {
  if (err instanceof ApiError) {
    if (err.network) return { alert: t('tf.eOffline') }
    if (err.status === 429) return { alert: t('tf.eMany') }
    if (err.status === 422) return { field: err.fieldErrors[field]?.[0] ?? fallback }
    return { alert: err.message }
  }
  return { alert: t('tf.eOffline') }
}

function Notice({ tone, icon, title, body }: { tone: 'warn' | 'live'; icon: ReactNode; title: string; body: string }) {
  return (
    <div className="tf-row-note">
      <p className={cx('tf-notice', `is-${tone}`)}>
        {icon}
        <span>
          <b>{title}.</b> {body}
        </span>
      </p>
    </div>
  )
}

/** Eight marks, one per recovery code; a used code is hollow. Low (≤ 2) turns them orange. */
function Marks({ left }: { left: number }) {
  const { t } = useTranslation(NS)
  return (
    <span className={cx('rc-marks', left <= LOW_CODES && 'is-low')} role="img" aria-label={t('tf.marksSr', { n: left })} data-testid="tf-marks">
      {Array.from({ length: TOTAL_CODES }, (_, i) => (
        <i key={i} className={i < left ? undefined : 'is-used'} />
      ))}
    </span>
  )
}

function madeText(at: number, lang: Lang): string {
  return `${formatDate(at, lang, true)}${lang === 'ar' ? '، ' : ', '}${formatTime(at, lang)}`
}

/** The recovery codes, set as a certificate under Atlas's own guilloché (design §4). */
function Certificate({ codes, email, madeAt, lang }: { codes: string[]; email: string; madeAt: number; lang: Lang }) {
  const { t } = useTranslation(NS)
  return (
    <figure className="rc-cert" aria-labelledby="rc-title" data-testid="tf-certificate">
      <span className="rc-engrave" aria-hidden="true">
        <Seal seed="atlas:recovery" size={360} />
      </span>
      <figcaption className="rc-head">
        <AppIcon size={30} />
        <span className="rc-head-text">
          <span className="rc-title" id="rc-title">
            {t('tf.certTitle')}
          </span>
          <bdi dir="ltr" className="rc-acct">
            {email}
          </bdi>
        </span>
      </figcaption>
      <ol className="rc-codes" data-testid="tf-codes">
        {codes.map((c, i) => (
          <li key={c}>
            <span className="rc-n">{i + 1}</span>
            <code dir="ltr">{c}</code>
          </li>
        ))}
      </ol>
      <p className="rc-foot">{t('tf.certMade', { date: madeText(madeAt, lang) })}</p>
    </figure>
  )
}

function codesFile(codes: string[], email: string, madeAt: number, lang: Lang, t: (k: string) => string): string {
  return [
    t('tf.certTitle'),
    email,
    madeText(madeAt, lang),
    '',
    t('tf.s3Text'),
    t('tf.certFile'),
    '',
    ...codes.map((c, i) => `${String(i + 1).padStart(2, ' ')}. ${c}`),
    '',
  ].join('\n')
}

function copyText(text: string, title: string, body?: string) {
  void navigator.clipboard?.writeText(text).catch(() => undefined)
  toast({ tone: 'success', title, body })
}

/* -------------------------------------------------------------------------------- the sheet */

function TwoFactorSheet({
  flow,
  setFlow,
  email,
  lang,
  onClosed,
}: {
  flow: Flow | null
  setFlow: (f: Flow | null) => void
  email: string
  lang: Lang
  onClosed: () => void
}) {
  const { t } = useTranslation(NS)
  const qc = useQueryClient()
  const [password, setPassword] = useState('')
  const [pwError, setPwError] = useState<string | null>(null)
  const [alert, setAlert] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [codeError, setCodeError] = useState<string | null>(null)
  const [shake, setShake] = useState(0)
  const [busy, setBusy] = useState(false)
  const pwRef = useRef<HTMLInputElement>(null)
  const codeRef = useRef<HTMLInputElement>(null)
  const savedRef = useRef<HTMLButtonElement>(null)
  const step = flow?.step

  // The sheet is remounted for each opening (keyed by the section), so state starts clean;
  // a step change only moves focus to that step's own control.
  useEffect(() => {
    const id = window.requestAnimationFrame(() => {
      if (step === 'password') pwRef.current?.focus()
      else if (step === 'scan') codeRef.current?.focus()
      else if (step === 'codes') savedRef.current?.focus()
    })
    return () => window.cancelAnimationFrame(id)
  }, [step, flow?.kind])

  if (!flow) return <Sheet open={false} title="" />

  const setStatus = (s: TwoFactorStatus) => qc.setQueryData(TWO_FACTOR_KEY, { data: s })
  const statusOf = (d: CodesResponse['data']): TwoFactorStatus => ({
    enabled: d.enabled,
    confirmed_at: d.confirmed_at,
    recovery_codes_left: d.recovery_codes_left,
  })

  async function submitPassword(e: FormEvent) {
    e.preventDefault()
    if (!flow || busy || !password) return
    setBusy(true)
    setAlert(null)
    setPwError(null)
    try {
      if (flow.kind === 'on') {
        const res = await api.post<{ data: { qr_svg: string; secret: string } }>('/api/me/two-factor/start', { password })
        setPassword('')
        setFlow({ kind: 'on', step: 'scan', qrSvg: res.data.qr_svg, secret: res.data.secret })
      } else if (flow.kind === 'regen') {
        const res = await api.post<CodesResponse>('/api/me/two-factor/recovery-codes', { password })
        setStatus(statusOf(res.data))
        setPassword('')
        setFlow({ kind: 'regen', step: 'codes', codes: res.data.recovery_codes, madeAt: Date.now() })
      } else {
        const res = await apiFetch<{ data: TwoFactorStatus }>('/api/me/two-factor', { method: 'DELETE', json: { password } })
        setStatus(res.data)
        setFlow(null)
        toast({ tone: 'success', title: t('tf.offToast'), body: t('tf.offToastBody') })
      }
    } catch (err) {
      const r = errorText(err, 'password', t, t('tf.ePw'))
      if (r.field) {
        setPwError(r.field)
        window.requestAnimationFrame(() => pwRef.current?.select())
      } else setAlert(r.alert ?? null)
      if (err instanceof ApiError && err.status === 409) void qc.invalidateQueries({ queryKey: TWO_FACTOR_KEY })
      window.requestAnimationFrame(() => pwRef.current?.focus())
    } finally {
      setBusy(false)
    }
  }

  async function submitCode(value: string) {
    if (busy) return
    if (value.length < OTP_LENGTH) {
      setCodeError(t('tf.eShort'))
      setShake((n) => n + 1)
      codeRef.current?.focus()
      return
    }
    setBusy(true)
    setCodeError(null)
    try {
      const res = await api.post<CodesResponse>('/api/me/two-factor/confirm', { code: value })
      setStatus(statusOf(res.data))
      setCode('')
      setFlow({ kind: 'on', step: 'codes', codes: res.data.recovery_codes, madeAt: Date.now() })
    } catch (err) {
      const r = errorText(err, 'code', t, t('tf.eCode'))
      setCode('')
      setCodeError(r.field ?? r.alert ?? t('tf.eCode'))
      setShake((n) => n + 1)
      window.requestAnimationFrame(() => codeRef.current?.focus())
    } finally {
      setBusy(false)
    }
  }

  function saved() {
    const kind = flow?.kind
    setFlow(null)
    if (kind === 'regen') toast({ tone: 'success', title: t('tf.rgToast'), body: t('tf.rgToastBody') })
    else toast({ tone: 'success', title: t('tf.onToast'), body: t('tf.onToastBody') })
  }

  const locked = flow.step === 'codes'
  const kind = flow.kind
  const stepNo = kind === 'on' ? (flow.step === 'password' ? 1 : flow.step === 'scan' ? 2 : 3) : null
  const icon =
    flow.step === 'codes' && kind === 'on' ? (
      <ShieldCheck strokeWidth={1.5} />
    ) : kind === 'off' ? (
      <ShieldX strokeWidth={1.5} />
    ) : kind === 'regen' ? (
      <KeyRound strokeWidth={1.5} />
    ) : (
      <Shield strokeWidth={1.5} />
    )
  const tone = kind === 'off' ? 'destructive' : flow.step === 'codes' && kind === 'on' ? 'profit' : 'accent'
  const title =
    flow.step === 'codes'
      ? t('tf.s3Title')
      : flow.step === 'scan'
        ? t('tf.s2Title')
        : kind === 'on'
          ? t('tf.s1Title')
          : kind === 'off'
            ? t('tf.offTitle')
            : t('tf.rgTitle')
  const description =
    flow.step === 'codes' ? (
      <>
        {kind === 'on' && (
          <span className="tf-done">
            <CircleCheck aria-hidden="true" strokeWidth={2} />
            {t('tf.s3On')}
          </span>
        )}{' '}
        {t('tf.s3Text')}
      </>
    ) : flow.step === 'password' ? (
      kind === 'on' ? (
        t('tf.s1Text')
      ) : kind === 'off' ? (
        t('tf.offText')
      ) : (
        t('tf.rgText')
      )
    ) : undefined

  const formId = 'tf-form'
  const goLabel = kind === 'on' ? t('tf.continue') : kind === 'off' ? t('tf.offGo') : t('tf.rgGo')

  let body: ReactNode
  let actions: ReactNode
  if (flow.step === 'password') {
    body = (
      <form id={formId} noValidate onSubmit={(e) => void submitPassword(e)} className="grid gap-3">
        {alert && (
          <p className="tf-alert" role="alert" data-testid="tf-alert">
            <CircleAlert aria-hidden="true" strokeWidth={1.5} />
            <span>{alert}</span>
          </p>
        )}
        <SecureField
          ref={pwRef}
          id="tf-pw"
          name="password"
          label={t('tf.pw')}
          autoComplete="current-password"
          value={password}
          disabled={busy}
          error={pwError ?? undefined}
          onChange={(e) => {
            setPassword(e.target.value)
            setPwError(null)
            setAlert(null)
          }}
        />
      </form>
    )
    actions = (
      <>
        <Button variant="secondary" disabled={busy} onClick={() => setFlow(null)}>
          {t('tf.cancel')}
        </Button>
        <Button
          type="submit"
          form={formId}
          variant={kind === 'off' ? 'destructive' : 'primary'}
          disabled={!password}
          loading={busy}
          data-testid="tf-pw-go"
        >
          {busy ? (kind === 'off' ? t('tf.offGoing') : t('tf.checking')) : goLabel}
        </Button>
      </>
    )
  } else if (flow.step === 'scan') {
    const qrSrc = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(flow.qrSvg)}`
    body = (
      <form
        id={formId}
        noValidate
        className="grid gap-3.5"
        onSubmit={(e) => {
          e.preventDefault()
          void submitCode(code)
        }}
      >
        <div className="tf-scan">
          <img className="tf-qr" src={qrSrc} alt={t('tf.qrAlt')} data-testid="tf-qr" />
          <ol className="tf-how">
            {(['s2l1', 's2l2', 's2l3'] as const).map((k, i) => (
              <li key={k}>
                <span className="tf-how-n" aria-hidden="true">
                  {i + 1}
                </span>
                <span>{t(`tf.${k}`)}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="tf-key">
          <p className="tf-key-label" id="tf-key-label">
            {t('tf.key')}
          </p>
          <span className="tf-key-chip">
            <code dir="ltr" aria-labelledby="tf-key-label" data-testid="tf-secret" data-secret={flow.secret}>
              {keyGroups(flow.secret)}
            </code>
            <Button size="sm" variant="plain" icon={<Copy strokeWidth={1.5} />} onClick={() => copyText(flow.secret, t('tf.keyCopied'))}>
              {t('tf.copy')}
            </Button>
          </span>
        </div>
        <div className="tf-code-row">
          <label className="tf-code-label" htmlFor="tf-code">
            {t('tf.code')}
          </label>
          <div className="tf-code-line">
            <OtpField
              ref={codeRef}
              id="tf-code"
              size="md"
              value={code}
              onChange={(v) => {
                setCode(v)
                if (codeError) setCodeError(null)
              }}
              onComplete={(v) => void submitCode(v)}
              error={codeError ?? undefined}
              shakeKey={shake}
              disabled={busy}
              describedBy="tf-dial-text"
            />
            <span className="otp-clock">
              <OtpDial />
              <span id="tf-dial-text">{t('tf.dial')}</span>
            </span>
          </div>
        </div>
      </form>
    )
    actions = (
      <>
        <Button variant="secondary" disabled={busy} onClick={() => setFlow(null)}>
          {t('tf.cancel')}
        </Button>
        <Button type="submit" form={formId} variant="primary" disabled={code.length < OTP_LENGTH} loading={busy} data-testid="tf-code-go">
          {busy ? t('tf.checking') : t('tf.turnOnGo')}
        </Button>
      </>
    )
  } else {
    const { codes, madeAt } = flow
    const download = () => {
      const url = URL.createObjectURL(new Blob([codesFile(codes, email, madeAt, lang, t)], { type: 'text/plain;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = 'atlas-recovery-codes.txt'
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast({ tone: 'success', title: t('tf.downloaded'), body: 'atlas-recovery-codes.txt' })
    }
    body = (
      <div className="grid gap-3">
        <Certificate codes={codes} email={email} madeAt={madeAt} lang={lang} />
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="secondary"
            icon={<Copy strokeWidth={1.5} />}
            onClick={() => copyText(codesFile(codes, email, madeAt, lang, t), t('tf.copied'), t('tf.copiedBody'))}
          >
            {t('tf.copyAll')}
          </Button>
          <Button size="sm" variant="secondary" icon={<Download strokeWidth={1.5} />} onClick={download} data-testid="tf-download">
            {t('tf.download')}
          </Button>
        </div>
      </div>
    )
    actions = (
      <Button ref={savedRef} variant="primary" onClick={saved} data-testid="tf-saved">
        {t('tf.saved')}
      </Button>
    )
  }

  return (
    <Sheet
      open
      onOpenChange={(next) => !next && !busy && setFlow(null)}
      locked={locked}
      role={locked ? 'alertdialog' : 'dialog'}
      className="tf-sheet-host"
      icon={icon}
      tone={tone}
      iconAside={stepNo ? <span className="tf-step">{t('tf.step', { n: stepNo })}</span> : undefined}
      title={title}
      description={description}
      actions={actions}
      onOpenAutoFocus={(e) => e.preventDefault()}
      onCloseAutoFocus={(e) => {
        e.preventDefault()
        onClosed()
      }}
    >
      <div key={`${kind}-${flow.step}`} className="tf-step-body" data-testid={`tf-step-${flow.step}`}>
        {body}
      </div>
    </Sheet>
  )
}

/* ------------------------------------------------------------------------------ the group */

export function TwoFactorSection({ email, lang }: { email: string; lang: Lang }) {
  const { t } = useTranslation(NS)
  const env = useTradingEnv()
  const status = useApiQuery<{ data: TwoFactorStatus }>(TWO_FACTOR_KEY, '/api/me/two-factor', { retry: false })
  const [flow, setFlowState] = useState<Flow | null>(null)
  const [opening, setOpening] = useState(0)
  const setFlow = (f: Flow | null) => {
    if (f && f.step === 'password') setOpening((n) => n + 1)
    setFlowState(f)
  }
  const groupRef = useRef<HTMLDivElement>(null)
  const head = (
    <h2 className="gn-group-title" id="tf-title">
      {t('tf.group')}
    </h2>
  )
  const focusRow = () => window.requestAnimationFrame(() => groupRef.current?.querySelector<HTMLButtonElement>('button')?.focus())

  let group: ReactNode
  let note: ReactNode = null
  if (status.isPending) {
    group = (
      <div className="st-group" aria-busy="true" data-testid="tf-loading">
        <div className="gn-row tf-row">
          <Skeleton className="size-7 rounded-[7px]" />
          <span className="gn-row-main">
            <Skeleton className="h-3.5 w-[32%]" />
            <Skeleton className="mt-1.5 h-3 w-[58%]" />
          </span>
          <Skeleton className="h-8 w-[84px] rounded-[10px]" />
        </div>
      </div>
    )
  } else if (status.isError || !status.data) {
    group = (
      <div className="st-group" ref={groupRef}>
        <div className="gn-row tf-row" role="alert" data-testid="tf-error">
          <span className="tf-icon is-error" aria-hidden="true">
            <CloudOff strokeWidth={1.5} />
          </span>
          <span className="gn-row-main">
            <span className="gn-row-label">{t('tf.loadErr')}</span>
            <span className="gn-row-sub">{t('tf.loadErrBody')}</span>
          </span>
          <Button variant="secondary" icon={<RefreshCw strokeWidth={1.5} />} onClick={() => void status.refetch()}>
            {t('tf.retry')}
          </Button>
        </div>
      </div>
    )
  } else if (!status.data.data.enabled) {
    const live = env === 'live'
    group = (
      <div className="st-group" ref={groupRef} data-testid="tf-off">
        <div className="gn-row tf-row">
          <span className="tf-icon" aria-hidden="true">
            <Shield strokeWidth={1.5} />
          </span>
          <span className="gn-row-main">
            <span className="gn-row-label">{t('tf.name')}</span>
            <span className="gn-row-sub">{t('tf.offSub')}</span>
          </span>
          <Button variant={live ? 'primary' : 'secondary'} onClick={() => setFlow({ kind: 'on', step: 'password' })} data-testid="tf-turn-on">
            {t('tf.turnOn')}
          </Button>
        </div>
        {live && <Notice tone="live" icon={<RadioTower aria-hidden="true" strokeWidth={1.5} />} title={t('tf.liveTitle')} body={t('tf.liveBody')} />}
      </div>
    )
    note = <p className="gn-group-note">{t('tf.noteOff')}</p>
  } else {
    const s = status.data.data
    const left = s.recovery_codes_left
    const low = left <= LOW_CODES
    group = (
      <div className="st-group" ref={groupRef} data-testid="tf-on">
        <div className="gn-row tf-row">
          <span className="tf-icon is-on" aria-hidden="true">
            <ShieldCheck strokeWidth={1.5} />
          </span>
          <span className="gn-row-main">
            <span className="tf-label-line">
              <span className="gn-row-label">{t('tf.name')}</span>
              <span className="tf-badge" data-testid="tf-badge">
                <Check aria-hidden="true" strokeWidth={2.5} />
                {t('tf.on')}
              </span>
            </span>
            <span className="gn-row-sub">{t('tf.onSub', { date: s.confirmed_at ? formatDate(s.confirmed_at, lang, true) : '' })}</span>
          </span>
          <Button variant="secondary" onClick={() => setFlow({ kind: 'off', step: 'password' })} data-testid="tf-turn-off">
            {t('tf.turnOff')}
          </Button>
        </div>
        <div className="gn-row tf-row">
          <span className={cx('tf-icon', low && 'is-warn')} aria-hidden="true">
            <KeyRound strokeWidth={1.5} />
          </span>
          <span className="gn-row-main">
            <span className="gn-row-label">{t('tf.codes')}</span>
            <span className="gn-row-sub tf-left">
              <Marks left={left} />
              <span data-testid="tf-left">{left === 0 ? t('tf.leftNone') : t('tf.left', { count: left })}</span>
            </span>
          </span>
          <Button variant={left === 0 ? 'primary' : 'secondary'} onClick={() => setFlow({ kind: 'regen', step: 'password' })} data-testid="tf-regen">
            {t('tf.newCodes')}
          </Button>
        </div>
        {low && (
          <Notice
            tone="warn"
            icon={<TriangleAlert aria-hidden="true" strokeWidth={1.5} />}
            title={left === 0 ? t('tf.noneTitle') : t('tf.low', { count: left })}
            body={left === 0 ? t('tf.noneBody') : t('tf.lowBody')}
          />
        )}
      </div>
    )
    note = (
      <p className="gn-group-note">
        {t('tf.noteOnBefore')} <code className="tf-cmd" dir="ltr">./atlas reset-two-factor</code> {t('tf.noteOnAfter')}
      </p>
    )
  }

  return (
    <section className="gn-section" aria-labelledby="tf-title" data-testid="tf-section">
      {head}
      {group}
      {note}
      <TwoFactorSheet key={opening} flow={flow} setFlow={setFlow} email={email} lang={lang} onClosed={focusRow} />
    </section>
  )
}
