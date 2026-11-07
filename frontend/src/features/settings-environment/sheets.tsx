import { Banknote, CalendarClock, CircleAlert, CircleCheck, FlaskConical, Hand, RadioTower, TriangleAlert } from 'lucide-react'
import { useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, SecureField, Sheet, TextField, toast } from '../../ui'
import { Notice } from '../onboarding'
import { refusal, useSwitchEnv } from './model'

/*
 * The two switch sheets (Live readiness §4.1, §4.2). To live: the owner's password and the typed
 * confirmation; Switch stays disabled until the password is not empty and the text is exactly
 * TRADE LIVE. Server answers show where they belong: the password's under the password, the
 * confirmation's under the confirmation, a pre-flight refusal or 429 in the alert slot.
 * To demo: one confirmation, no password.
 */

const FORM_ID = 'env-switch-live'

function scrollContentTop() {
  document.querySelector('.shell-content')?.scrollTo?.({ top: 0 })
}

export function SwitchToLiveSheet({
  confirmationText,
  uidLast4,
  onClose,
}: {
  confirmationText: string
  uidLast4: string | null
  onClose: () => void
}) {
  const { t } = useTranslation('settings-environment')
  const sw = useSwitchEnv()
  const pwRef = useRef<HTMLInputElement>(null)
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pwError, setPwError] = useState<string | null>(null)
  const [cfError, setCfError] = useState<string | null>(null)
  const [alert, setAlert] = useState<ReactNode>(null)
  const expected = confirmationText || 'TRADE LIVE'
  const exact = confirmation === expected
  const canGo = password.length > 0 && exact && !sw.isPending

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!canGo) return
    setPwError(null)
    setCfError(null)
    setAlert(null)
    sw.mutate(
      { target: 'live', password, confirmation },
      {
        onSuccess: () => {
          onClose()
          scrollContentTop()
          toast({ tone: 'success', title: t('sw.toast'), body: t('sw.toastBody') })
        },
        onError: (err) => {
          if (err.status === 429) {
            setAlert(
              <Notice tone="error" icon={CircleAlert} title={t('sw.many')} role="alert" />,
            )
            return
          }
          if (err.status === 422) {
            const r = refusal(err)
            if (r.errors.password?.[0]) {
              setPwError(r.errors.password[0])
              requestAnimationFrame(() => {
                pwRef.current?.focus()
                pwRef.current?.select()
              })
              return
            }
            if (r.errors.confirmation?.[0]) setCfError(r.errors.confirmation[0])
            if (r.errors.preflight || r.failing_items.length > 0) {
              const names = r.failing_items.map((k) => t(`i.${k}`)).join(', ')
              setAlert(
                <Notice
                  tone="warning"
                  icon={TriangleAlert}
                  title={names ? t('sw.pre', { items: names }) : (r.errors.preflight?.[0] ?? '')}
                  role="alert"
                />,
              )
            }
            if (!r.errors.confirmation && !r.errors.preflight && r.failing_items.length === 0) {
              setAlert(<Notice tone="error" icon={CircleAlert} title={err.message} role="alert" />)
            }
            return
          }
          setAlert(<Notice tone="error" icon={CircleAlert} title={t('sw.failed')} role="alert" />)
        },
      },
    )
  }

  return (
    <Sheet
      open
      onOpenChange={(open) => !open && !sw.isPending && onClose()}
      tone="live"
      icon={<RadioTower strokeWidth={1.5} />}
      title={
        <span className="inline-flex flex-wrap items-center gap-2">
          <span>{t('sw.title')}</span>
          <span className="env-tag">{t('sw.liveTag')}</span>
        </span>
      }
      description={t('sw.text', { uid: uidLast4 ? `⁦•••• ${uidLast4}⁩` : '' }).replace('  ', ' ')}
      onOpenAutoFocus={(e) => {
        e.preventDefault()
        pwRef.current?.focus()
      }}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={sw.isPending}>
            {t('cancel')}
          </Button>
          <Button
            type="submit"
            form={FORM_ID}
            variant="secondary"
            className="env-btn-live"
            disabled={!canGo && !sw.isPending}
            loading={sw.isPending}
          >
            {t('sw.go')}
          </Button>
        </>
      }
    >
      <form id={FORM_ID} className="grid gap-4" noValidate onSubmit={submit}>
        <ul className="env-sw-facts">
          <li>
            <CalendarClock aria-hidden="true" strokeWidth={1.75} />
            <span>{t('sw.f1')}</span>
          </li>
          <li>
            <Banknote aria-hidden="true" strokeWidth={1.75} />
            <span>{t('sw.f2')}</span>
          </li>
          <li>
            <Hand aria-hidden="true" strokeWidth={1.75} />
            <span>{t('sw.f3')}</span>
          </li>
        </ul>
        {alert && <div data-testid="env-sw-alert">{alert}</div>}
        <SecureField
          ref={pwRef}
          id="env-pw"
          name="password"
          label={t('sw.pw')}
          autoComplete="current-password"
          value={password}
          disabled={sw.isPending}
          error={pwError ?? undefined}
          onChange={(e) => {
            setPassword(e.target.value)
            setPwError(null)
            setAlert(null)
          }}
        />
        <div className="env-confirm-field" data-match={exact || undefined}>
          <TextField
            id="env-confirm"
            name="confirmation"
            label={t('sw.confirm')}
            hint={t('sw.confirmHint')}
            error={cfError ?? undefined}
            value={confirmation}
            disabled={sw.isPending}
            dir="ltr"
            lang="en"
            autoComplete="off"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            placeholder={expected}
            inputClassName="env-confirm"
            endSlot={
              <span className="env-match" aria-hidden="true">
                <CircleCheck />
              </span>
            }
            onChange={(e) => {
              setConfirmation(e.target.value)
              setCfError(null)
              setAlert(null)
            }}
          />
        </div>
      </form>
    </Sheet>
  )
}

export function SwitchToDemoSheet({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation('settings-environment')
  const sw = useSwitchEnv()
  const [alert, setAlert] = useState<string | null>(null)
  const go = () => {
    setAlert(null)
    sw.mutate(
      { target: 'demo', confirm: true },
      {
        onSuccess: () => {
          onClose()
          scrollContentTop()
          toast({ tone: 'success', title: t('dm.toast'), body: t('sw.toastBody') })
        },
        onError: (err) => setAlert(err.status === 429 ? t('sw.many') : err.network ? t('sw.failed') : err.message),
      },
    )
  }
  return (
    <Sheet
      open
      onOpenChange={(open) => !open && !sw.isPending && onClose()}
      tone="demo"
      icon={<FlaskConical strokeWidth={1.5} />}
      title={t('dm.title')}
      description={`${t('dm.text')} ${t('dm.text2')}`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={sw.isPending}>
            {t('cancel')}
          </Button>
          <Button variant="primary" onClick={go} loading={sw.isPending}>
            {t('dm.go')}
          </Button>
        </>
      }
    >
      {alert && <Notice tone="error" icon={CircleAlert} title={alert} role="alert" />}
    </Sheet>
  )
}
