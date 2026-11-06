import { ChevronLeft, ChevronRight, Clock } from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../lib/api'
import { Button, OtpDial, OtpField, OTP_LENGTH, TextField, toast } from '../../ui'
import { useDir } from '../../ui/useDir'
import { FADE } from '../../ui/motion'
import { CommandChip, Notice } from './loginParts'
import { challengeErrorKind, retryAfterSeconds, useTwoFactorChallenge, type ChallengeAnswer } from './session'

const RESET_COMMAND = './atlas reset-two-factor'
/** A recovery-code sign-in at or below this many codes left warns, like the Settings marks. */
const LOW_CODES = 2

export type CodeMode = 'code' | 'recovery'

interface TwoFactorStepProps {
  mode: CodeMode
  onModeChange: (mode: CodeMode) => void
  /** Signed in: continue exactly as a normal sign-in does. */
  onSignedIn: () => void
  /** No live challenge any more (5 minutes passed): back to the password with a notice. */
  onExpired: () => void
  /** "Back to password": the owner gives up on this code step. */
  onBack: () => void
}

/**
 * The login's second step (ATL-248, design v22 §5.4), shown after POST /api/auth/login answered
 * `{ two_factor: true }` or after a reload while /api/auth/status says `two_factor_pending`.
 * Six digits auto-submit to POST /api/auth/two-factor-challenge; "Use a recovery code instead"
 * swaps in one text field that posts `recovery_code`.
 */
export function TwoFactorStep({ mode, onModeChange, onSignedIn, onExpired, onBack }: TwoFactorStepProps) {
  const { t } = useTranslation(['auth', 'common'])
  const dir = useDir()
  const challenge = useTwoFactorChallenge()
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [shake, setShake] = useState(0)
  const [retryIn, setRetryIn] = useState(0)
  const [failed, setFailed] = useState<'down' | 'other' | null>(null)
  const [lostOpen, setLostOpen] = useState(false)
  const codeRef = useRef<HTMLInputElement>(null)
  const recoveryRef = useRef<HTMLInputElement>(null)

  const throttled = retryIn > 0
  const busy = challenge.isPending

  useEffect(() => {
    if (retryIn <= 0) return
    const id = setTimeout(() => setRetryIn((s) => Math.max(0, s - 1)), 1000)
    return () => clearTimeout(id)
  }, [retryIn])

  // Focus the field whenever it becomes usable again: a mode switch, a wrong code, a 429 ending.
  useEffect(() => {
    if (busy || throttled) return
    ;(mode === 'code' ? codeRef : recoveryRef).current?.focus({ preventScroll: true })
  }, [mode, busy, throttled, shake])

  function send(answer: ChallengeAnswer) {
    if (busy || throttled) return
    const viaRecovery = 'recovery_code' in answer
    setError(undefined)
    setFailed(null)
    challenge.mutate(answer, {
      onSuccess: () => {
        onSignedIn()
        if (viaRecovery) void announceCodesLeft(t)
      },
      onError: (err) => {
        const kind = challengeErrorKind(err)
        if (kind === 'challenge_expired') return onExpired()
        if (kind === 'code_wrong') {
          if (viaRecovery) {
            setError(t('tf.recoveryWrong'))
          } else {
            setCode('')
            setError(t('tf.codeWrong'))
          }
          setShake((n) => n + 1)
          if (viaRecovery) requestAnimationFrame(() => recoveryRef.current?.select())
        } else if (err.status === 429) {
          setCode('')
          setRetryIn(retryAfterSeconds(err))
        } else {
          setFailed(err.network ? 'down' : 'other')
        }
      },
    })
  }

  function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (code.length < OTP_LENGTH) {
      setError(t('tf.short'))
      setShake((n) => n + 1)
      return
    }
    send({ code })
  }

  function submitRecovery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = recovery.trim()
    if (!value) {
      setError(t('tf.empty'))
      recoveryRef.current?.focus()
      return
    }
    send({ recovery_code: value })
  }

  function switchMode(next: CodeMode) {
    setError(undefined)
    setFailed(null)
    onModeChange(next)
  }

  const waitLine = throttled && (
    <p role="status" className="flex items-center justify-center gap-1.5 text-footnote font-medium text-warning-ink">
      <Clock aria-hidden="true" className="size-3.5 shrink-0" strokeWidth={1.5} />
      <span>{t('tf.many', { s: retryIn })}</span>
    </p>
  )
  const verifyButton = (
    <Button type="submit" variant="primary" size="lg" className="mt-1 w-full" loading={busy} disabled={throttled}>
      {busy ? t('tf.verifying') : t('tf.verify')}
    </Button>
  )
  const backButton = (
    <Button variant="plain" size="sm" className="text-label-secondary" icon={<ChevronLeft className="rtl:-scale-x-100" />} onClick={onBack}>
      {t('tf.back')}
    </Button>
  )

  return (
    <motion.div
      key={mode}
      className="grid gap-4"
      initial={{ opacity: 0, x: dir === 'rtl' ? -16 : 16 }}
      animate={{ opacity: 1, x: 0 }}
      transition={FADE}
      data-testid="two-factor-step"
    >
      {failed === 'down' && <Notice tone="error" title={t('down.title')} body={t('down.body')} />}
      {failed === 'other' && <Notice tone="error" title={t('failed')} />}

      {mode === 'code' ? (
        <>
          <form noValidate onSubmit={submitCode} className="grid justify-items-center gap-4 text-center" aria-busy={busy || undefined}>
            <OtpField
              ref={codeRef}
              id="login-code"
              size="lg"
              value={code}
              onChange={(v) => {
                setCode(v)
                if (error) setError(undefined)
              }}
              onComplete={(v) => send({ code: v })}
              error={error}
              shakeKey={shake}
              disabled={busy || throttled}
              autoFocus
              describedBy={throttled ? undefined : 'login-dial'}
            />
            {waitLine || (
              <span id="login-dial" className="-mt-1 inline-flex items-center gap-2 text-footnote text-label-secondary">
                <OtpDial />
                <span>{t('tf.dial')}</span>
              </span>
            )}
            {verifyButton}
          </form>
          <div className="-mt-0.5 grid justify-items-center gap-0.5">
            <Button variant="plain" onClick={() => switchMode('recovery')}>
              {t('tf.useRecovery')}
            </Button>
            {backButton}
          </div>
        </>
      ) : (
        <>
          <form noValidate onSubmit={submitRecovery} className="grid gap-4" aria-busy={busy || undefined}>
            <TextField
              ref={recoveryRef}
              id="login-recovery"
              name="recovery_code"
              label={t('tf.recovery')}
              hint={t('tf.recoveryHint')}
              error={error}
              dir="ltr"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder="xxxxx-xxxxx"
              inputClassName="text-center font-mono tracking-[0.06em]"
              value={recovery}
              disabled={busy || throttled}
              onChange={(e) => {
                setRecovery(e.target.value)
                if (error) setError(undefined)
              }}
            />
            {waitLine}
            {verifyButton}
          </form>
          <div className="grid justify-items-center">
            <Button variant="plain" onClick={() => switchMode('code')}>
              {t('tf.useApp')}
            </Button>
          </div>
          <div className="text-start">
            <button
              type="button"
              aria-expanded={lostOpen}
              aria-controls="login-lost"
              onClick={() => setLostOpen((o) => !o)}
              className="-ms-1.5 inline-flex items-center gap-1 rounded-[6px] px-1.5 py-1 text-footnote font-medium text-accent-ink hover:bg-fill-4 focus-visible:shadow-focus"
            >
              <ChevronRight
                aria-hidden="true"
                strokeWidth={1.5}
                className={'size-3.5 transition-transform ' + (lostOpen ? 'rotate-90' : 'rtl:-scale-x-100')}
              />
              <span>{t('tf.lost')}</span>
            </button>
            <div
              id="login-lost"
              hidden={!lostOpen}
              className="mt-2 grid justify-items-start gap-2.5 rounded-card bg-background-elevated px-4 py-3.5 shadow-[0_0_0_0.5px_var(--hairline-color),var(--ui-shadow-1)]"
            >
              <p className="text-footnote text-label-secondary">{t('tf.lostBody')}</p>
              <CommandChip command={RESET_COMMAND} small />
              <p className="text-footnote text-label-secondary">{t('tf.lostAfter')}</p>
            </div>
          </div>
          <div className="grid justify-items-center">{backButton}</div>
        </>
      )}
    </motion.div>
  )
}

/**
 * After a recovery-code sign-in, tell the owner how many codes are left (design v22 §5.4).
 * A failed status read is not worth a second message: the owner is already in.
 */
async function announceCodesLeft(t: (key: string, opts?: Record<string, unknown>) => string) {
  try {
    const res = await api.get<{ data: { recovery_codes_left: number } }>('/api/me/two-factor')
    const left = res.data.recovery_codes_left
    toast({
      tone: left <= LOW_CODES ? 'warning' : 'info',
      title: t('tf.toast'),
      body: left > 0 ? t('tf.toastBody', { n: left }) : t('tf.toastNone'),
      durationMs: 7000,
    })
  } catch (err) {
    if (!(err instanceof ApiError)) throw err
  }
}
