import { Check, Clock, Copy, Info, Lock, RefreshCw, ServerOff } from 'lucide-react'
import { MotionConfig, motion } from 'motion/react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { RiskFooter } from '../../app/shell/RiskFooter'
import { LanguageButton, ThemeMenu, ToolbarGroup } from '../../app/shell/Prefs'
import { ApiError } from '../../lib/api'
import { AppIcon, Button, SecureField, Seal, TextField } from '../../ui'
import { FADE } from '../../ui/motion'
import { safeNextPath, useAuthStatus, useLogin, useSession } from './session'

const CREATE_OWNER_COMMAND = './atlas create-owner'
const COPIED_FEEDBACK_MS = 2000

interface FieldErrors {
  email?: string
  password?: string
}

/**
 * /login (design: js/pages/login.js, css/auth.css). Centred 380px column over a faint
 * seal: app icon, "Atlas", the form. States: first run (no owner yet), wrong password,
 * throttled (429 countdown), locked after idle, server unreachable.
 */
export function LoginPage() {
  const { t } = useTranslation(['auth', 'common'])
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const next = safeNextPath(params.get('next'))
  const idleLocked = params.get('reason') === 'idle'

  const session = useSession()
  const status = useAuthStatus()
  const login = useLogin()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<FieldErrors>({})
  const [retryIn, setRetryIn] = useState(0)
  const [failed, setFailed] = useState(false)
  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  // Throttle countdown from the 429's Retry-After.
  useEffect(() => {
    if (retryIn <= 0) return
    const id = setTimeout(() => setRetryIn((s) => Math.max(0, s - 1)), 1000)
    return () => clearTimeout(id)
  }, [retryIn])

  const firstRun = status.data?.owner_exists === false
  const serverDown =
    (status.isError && status.error.network) || (login.isError && login.error.network) || (session.isError && session.error.network)

  useEffect(() => {
    if (firstRun) return
    if (errors.password) passwordRef.current?.focus()
    else if (errors.email) emailRef.current?.focus()
  }, [errors, firstRun])

  if (session.data) return <Navigate to={next} replace />

  const throttled = retryIn > 0
  const busy = login.isPending

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || throttled) return
    const found: FieldErrors = {}
    if (!email.trim()) found.email = t('needEmail')
    if (!password) found.password = t('needPassword')
    setErrors(found)
    setFailed(false)
    if (found.email || found.password) return

    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: () => {
          void navigate(next, { replace: true })
        },
        onError: (err) => {
          if (err.status === 422) {
            // Laravel reports wrong credentials on `email`; the design shows it under the
            // password, keeps the email and clears only the password.
            setPassword('')
            setErrors({ password: t('wrong') })
          } else if (err.status === 429) {
            setPassword('')
            setRetryIn(retryAfterSeconds(err))
          } else if (!err.network) {
            setFailed(true)
          }
        },
      },
    )
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="relative flex min-h-dvh flex-col overflow-hidden bg-background">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 -z-0 bg-[radial-gradient(70%_60%_at_50%_42%,var(--wash-1),transparent_72%)]"
        />
        <ToolbarGroup className="absolute end-4 top-3 z-3">
          <LanguageButton />
          <ThemeMenu />
        </ToolbarGroup>
        <div className="relative grid flex-1 place-items-center px-6 pt-18 pb-14">
          <main aria-labelledby="auth-title" className="relative z-1 grid w-full max-w-[380px] gap-6">
            <Seal
              seed="atlas"
              size={780}
              variant="band"
              className="pointer-events-none absolute start-1/2 top-1/2 -z-1 -translate-y-1/2 opacity-32 ltr:-translate-x-1/2 rtl:translate-x-1/2 dark:opacity-42"
            />
            <motion.div
              className="grid justify-items-center gap-1.5 text-center"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={FADE}
            >
              <AppIcon size={72} className="app-icon-hero mb-2.5" />
              <h1 id="auth-title" className="text-large-title text-label">
                {t('common:appName')}
              </h1>
              <p className="text-callout text-label-secondary">{firstRun ? t('first.title') : t('lead')}</p>
            </motion.div>

            <motion.div
              className="grid gap-6"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...FADE, delay: 0.1 }}
            >
              {firstRun ? (
                <FirstRun onCheck={() => void status.refetch()} checking={status.isFetching} />
              ) : (
                <>
                  {serverDown ? (
                    <Notice tone="error" icon={<ServerOff />} title={t('down.title')} body={t('down.body')} />
                  ) : idleLocked ? (
                    <Notice tone="info" icon={<Lock />} title={t('locked.title')} body={t('locked.body')} />
                  ) : null}
                  {failed && <Notice tone="error" icon={<Info />} title={t('failed')} />}
                  <form noValidate onSubmit={submit} className="grid gap-4" aria-busy={busy || undefined}>
                    <TextField
                      ref={emailRef}
                      label={t('email')}
                      type="email"
                      name="email"
                      dir="ltr"
                      autoComplete="username"
                      placeholder={t('emailPlaceholder')}
                      autoFocus
                      value={email}
                      disabled={busy}
                      error={errors.email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                    <SecureField
                      ref={passwordRef}
                      label={t('password')}
                      name="password"
                      autoComplete="current-password"
                      value={password}
                      disabled={busy}
                      error={errors.password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                    {throttled && (
                      <p role="status" className="flex items-center gap-1.5 text-footnote font-medium text-warning-ink">
                        <Clock aria-hidden="true" className="size-3.5 shrink-0" strokeWidth={1.5} />
                        <span>{t('throttled', { s: retryIn })}</span>
                      </p>
                    )}
                    <Button type="submit" variant="primary" size="lg" className="mt-1.5 w-full" loading={busy} disabled={throttled}>
                      {busy ? t('signingIn') : t('signIn')}
                    </Button>
                  </form>
                </>
              )}
            </motion.div>
          </main>
        </div>
        <RiskFooter />
      </div>
    </MotionConfig>
  )
}

function retryAfterSeconds(err: ApiError): number {
  const header = Number(err.headers?.get('Retry-After'))
  return Number.isFinite(header) && header > 0 ? Math.ceil(header) : 60
}

function Notice({ tone, icon, title, body }: { tone: 'info' | 'error'; icon: ReactNode; title: string; body?: string }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={
        'flex items-start gap-3 rounded-card px-4 py-3.5 text-start shadow-[inset_0_0_0_0.5px_var(--hairline-color)] ' +
        (tone === 'error' ? 'bg-loss-wash' : 'bg-fill-4')
      }
    >
      <span aria-hidden="true" className={'mt-px shrink-0 [&_svg]:size-5 [&_svg]:stroke-[1.5] ' + (tone === 'error' ? 'text-loss-ink' : 'text-accent')}>
        {icon}
      </span>
      <div>
        <p className="text-subheadline font-semibold text-label">{title}</p>
        {body && <p className="mt-0.5 text-footnote text-label-secondary">{body}</p>}
      </div>
    </div>
  )
}

function FirstRun({ onCheck, checking }: { onCheck: () => void; checking: boolean }) {
  const { t } = useTranslation('auth')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS)
    return () => clearTimeout(id)
  }, [copied])

  async function copy() {
    try {
      await navigator.clipboard.writeText(CREATE_OWNER_COMMAND)
      setCopied(true)
    } catch {
      // Clipboard blocked: the command stays selectable on screen.
    }
  }

  return (
    <div className="grid justify-items-start gap-3.5 rounded-panel bg-background-elevated p-5 text-start shadow-[0_0_0_0.5px_var(--hairline-color),var(--ui-shadow-2)]">
      <p className="text-subheadline text-label">{t('first.body')}</p>
      <span
        dir="ltr"
        className="inline-flex items-center gap-2 rounded-[8px] bg-background-secondary py-2 ps-3.5 pe-2 font-mono text-subheadline text-label shadow-[inset_0_0_0_0.5px_var(--hairline-color)]"
      >
        <code className="select-all">{CREATE_OWNER_COMMAND}</code>
        <button
          type="button"
          onClick={() => void copy()}
          aria-label={copied ? t('first.copied') : t('first.copy')}
          title={copied ? t('first.copied') : t('first.copy')}
          className="grid size-[26px] place-items-center rounded-[6px] text-label-secondary hover:bg-fill-2 hover:text-label focus-visible:shadow-focus"
        >
          {copied ? (
            <Check aria-hidden="true" className="size-4 text-profit-ink" strokeWidth={1.5} />
          ) : (
            <Copy aria-hidden="true" className="size-4" strokeWidth={1.5} />
          )}
        </button>
      </span>
      <p role="status" className="sr-only">
        {copied ? t('first.copied') : ''}
      </p>
      <p className="text-subheadline text-label-secondary">{t('first.after')}</p>
      <Button size="lg" className="mt-1 w-full" icon={<RefreshCw />} loading={checking} onClick={onCheck}>
        {t('first.check')}
      </Button>
    </div>
  )
}
