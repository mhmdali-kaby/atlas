import { Check, CircleAlert, CircleCheck, Replace, Send, Trash2 } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../lib/api'
import { Button, SecureField, Sheet, SheetClose, TextField, toast } from '../../ui'
import { Notice } from '../onboarding'
import { useLang } from '../portfolio/hooks'
import { formatClock, formatDayTime } from '../portfolio/time'
import { iso } from '../notifications/events'
import { masked, serverFieldErrors, useRemoveBot, useSaveBot, useTestBot, validateBot, type TelegramState } from './model'
import { Fig } from './parts'

/*
 * The Telegram group (design §6.3). The typed token lives only in its uncontrolled <input>, never
 * in React state, a query cache or a log: it is read once on submit and the field is emptied after
 * a save. Saved values come back from the API as last 4 only.
 */

const NS = 'settings-notifications'

type Errors = { token?: string; chat?: string; form?: string }

/** Validates, PUTs, and maps 422s back to the fields; resolves true once saved. */
function useBotForm(onSaved: (state: TelegramState) => void) {
  const { t } = useTranslation(NS)
  const save = useSaveBot()
  const tokenRef = useRef<HTMLInputElement>(null)
  const chatRef = useRef<HTMLInputElement>(null)
  const [errors, setErrors] = useState<Errors>({})

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const token = tokenRef.current?.value.trim() ?? ''
    const chat = chatRef.current?.value.trim() ?? ''
    const local = validateBot(token, chat)
    if (local.token || local.chat) {
      setErrors({
        token: local.token && t(`tg.e.${local.token}`),
        chat: local.chat && t(`tg.e.${local.chat}`),
      })
      ;(local.token ? tokenRef : chatRef).current?.focus()
      return
    }
    setErrors({})
    try {
      const res = await save.mutateAsync({
        bot_token: token,
        chat_id: chat,
      })
      if (tokenRef.current) tokenRef.current.value = ''
      if (chatRef.current) chatRef.current.value = ''
      onSaved(res.data)
    } catch (err) {
      const server = serverFieldErrors(err)
      if (server.bot_token || server.chat_id) {
        setErrors({ token: server.bot_token, chat: server.chat_id })
        ;(server.bot_token ? tokenRef : chatRef).current?.focus()
      } else {
        setErrors({ form: t('tg.e.server') })
      }
    }
  }
  return { submit, errors, busy: save.isPending, tokenRef, chatRef }
}

function BotFields({
  prefix,
  errors,
  busy,
  tokenRef,
  chatRef,
}: {
  prefix: string
  errors: Errors
  busy: boolean
  tokenRef: RefObject<HTMLInputElement | null>
  chatRef: RefObject<HTMLInputElement | null>
}) {
  const { t } = useTranslation(NS)
  return (
    <div className="ns-tg-fields">
      <SecureField
        ref={tokenRef}
        id={`${prefix}-token`}
        name="bot_token"
        label={t('tg.token')}
        placeholder="123456789:AAH…"
        hint={errors.token ? undefined : t('tg.tokenHint')}
        error={errors.token}
        disabled={busy}
        data-testid={`${prefix}-token`}
      />
      <TextField
        ref={chatRef}
        id={`${prefix}-chat`}
        name="chat_id"
        label={t('tg.chat')}
        placeholder="987654321"
        dir="ltr"
        inputMode="numeric"
        autoComplete="off"
        error={errors.chat}
        disabled={busy}
        data-testid={`${prefix}-chat`}
      />
    </div>
  )
}

function SetupForm({ onSaved }: { onSaved: (state: TelegramState) => void }) {
  const { t } = useTranslation(NS)
  const form = useBotForm(onSaved)
  return (
    <div className="st-group ns-tg-setup" data-testid="tg-setup">
      <div className="ns-tg-intro">
        <span className="ns-tg-mark" aria-hidden="true">
          <Send strokeWidth={1.5} />
        </span>
        <p>{t('tg.intro')}</p>
      </div>
      <ol className="ns-steps">
        {(['s1', 's2', 's3'] as const).map((k, i) => (
          <li key={k}>
            <span className="ns-step-n" aria-hidden="true">
              <Fig>{i + 1}</Fig>
            </span>
            <span dir="auto">{t(`tg.${k}`)}</span>
          </li>
        ))}
      </ol>
      <form className="ns-tg-form" noValidate onSubmit={(e) => void form.submit(e)} aria-label={t('tg.title')}>
        <BotFields prefix="ns-tg" {...form} />
        {form.errors.form && <Notice tone="error" icon={CircleAlert} title={form.errors.form} role="alert" />}
        <div className="flex">
          <Button type="submit" variant="primary" loading={form.busy} icon={<Send strokeWidth={1.5} />} data-testid="tg-save">
            {t(form.busy ? 'tg.saving' : 'tg.save')}
          </Button>
        </div>
      </form>
    </div>
  )
}

function ReplaceSheet({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onSaved: (s: TelegramState) => void
}) {
  const { t } = useTranslation(NS)
  const formId = useId()
  const form = useBotForm((s) => {
    onOpenChange(false)
    onSaved(s)
  })
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !form.busy && onOpenChange(next)}
      onOpenAutoFocus={(e) => {
        e.preventDefault()
        form.tokenRef.current?.focus()
      }}
      icon={<Send strokeWidth={1.5} />}
      title={t('tg.rpTitle')}
      description={t('tg.rpText')}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={form.busy}>
              {t('tg.cancel')}
            </Button>
          </SheetClose>
          <Button type="submit" form={formId} variant="primary" loading={form.busy} data-testid="tg-rp-save">
            {t(form.busy ? 'tg.saving' : 'tg.save')}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={(e) => void form.submit(e)} className="grid gap-3">
        <BotFields prefix="ns-rp" {...form} />
        {form.errors.form && <Notice tone="error" icon={CircleAlert} title={form.errors.form} role="alert" />}
      </form>
    </Sheet>
  )
}

function RemoveSheet({ open, onOpenChange, onRemoved }: { open: boolean; onOpenChange: (o: boolean) => void; onRemoved: () => void }) {
  const { t } = useTranslation(NS)
  const remove = useRemoveBot()
  const go = async () => {
    try {
      await remove.mutateAsync()
      onOpenChange(false)
      onRemoved()
      toast({
        tone: 'success',
        title: t('tg.rmToast'),
        body: t('tg.rmToastBody'),
      })
    } catch {
      toast({
        tone: 'error',
        title: t('tg.rmFail'),
        body: t('err.body'),
      })
    }
  }
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !remove.isPending && onOpenChange(next)}
      tone="destructive"
      icon={<Trash2 strokeWidth={1.5} />}
      title={t('tg.rmTitle')}
      description={t('tg.rmText')}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={remove.isPending}>
              {t('tg.cancel')}
            </Button>
          </SheetClose>
          <Button variant="destructive" loading={remove.isPending} onClick={() => void go()} data-testid="tg-rm-go">
            {t('tg.rmDo')}
          </Button>
        </>
      }
    />
  )
}

export function TelegramSection({
  state,
  onUseNeeds,
  needsOn,
}: {
  state: TelegramState
  /** Turns on telegram for the four "Needs you" events (the first-save notice's one button). */
  onUseNeeds: () => void
  needsOn: boolean
}) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const test = useTestBot()
  const [justSaved, setJustSaved] = useState(false)
  const [tested, setTested] = useState<{ ok: true; at: string } | { ok: false; error: string } | null>(null)
  const [sheet, setSheet] = useState<'replace' | 'remove' | null>(null)
  const testRef = useRef<HTMLButtonElement>(null)

  const saved = (s: TelegramState) => {
    setJustSaved(true)
    setTested(null)
    toast({
      tone: 'success',
      title: t('tg.savedToast'),
      body: t('tg.savedToastBody', {
        last4: masked(s.bot_token_last4 ?? ''),
      }),
    })
    requestAnimationFrame(() => testRef.current?.focus())
  }

  if (!state.configured) return <SetupForm onSaved={saved} />

  const runTest = async () => {
    setTested(null)
    try {
      const res = await test.mutateAsync()
      if (res.ok) {
        setTested({
          ok: true,
          at: res.data?.last_tested_at ?? new Date().toISOString(),
        })
        toast({
          tone: 'success',
          title: t('tg.testOkToast'),
          body: t('tg.testOkBody'),
        })
      } else {
        setTested({
          ok: false,
          error: res.data?.last_error ?? res.message ?? '',
        })
      }
    } catch (err) {
      setTested({
        ok: false,
        error: err instanceof ApiError ? err.message : '',
      })
    }
  }

  const when = (ts: string | null, none: string) => (ts ? <span className="tabular-nums">{formatDayTime(ts, lang)}</span> : t(none))

  return (
    <div className="st-group" data-testid="tg-connected">
      <div className="ns-tg-top">
        <span className="ns-tg-mark is-on" aria-hidden="true">
          <Send strokeWidth={1.5} />
        </span>
        <div className="ns-tg-id">
          <p className="ns-tg-bot" data-testid="tg-bot">
            {t('tg.bot', {
              last4: masked(state.bot_token_last4 ?? ''),
            })}
          </p>
          <p className="ns-tg-to" data-testid="tg-chat">
            {t('tg.sendsTo', {
              last4: masked(state.chat_id_last4 ?? ''),
            })}
          </p>
        </div>
        <span className="ns-badge-ok">
          <CircleCheck aria-hidden="true" strokeWidth={1.75} />
          {t('tg.connected')}
        </span>
      </div>
      <dl className="ns-tg-rows">
        <div className="ns-tg-row">
          <dt>{t('tg.lastSent')}</dt>
          <dd>{when(state.last_sent_at, 'tg.never')}</dd>
        </div>
        <div className="ns-tg-row">
          <dt>{t('tg.lastTest')}</dt>
          <dd>{when(state.last_tested_at, 'tg.untested')}</dd>
        </div>
      </dl>
      {tested?.ok && (
        <p className="ns-tg-status" role="status" data-testid="tg-test-ok">
          <CircleCheck aria-hidden="true" strokeWidth={1.75} />
          <span>
            {t('tg.testOk', {
              time: iso(formatClock(tested.at, lang, false)),
            })}
          </span>
        </p>
      )}
      {tested && !tested.ok && (
        <Notice
          tone="error"
          icon={CircleAlert}
          title={t('tg.testFailTitle')}
          role="alert"
          className="ns-tg-notice"
          data-testid="tg-test-fail"
        >
          {t('tg.testFailBody')}
          {tested.error && (
            <span className="ns-tg-err block" dir="ltr">
              {tested.error}
            </span>
          )}
        </Notice>
      )}
      {justSaved && (
        <Notice
          tone="success"
          icon={CircleCheck}
          title={t('tg.savedTitle')}
          role="status"
          className="ns-tg-notice"
          actions={
            needsOn ? (
              <span className="ns-used">
                <Check aria-hidden="true" strokeWidth={2} />
                <span>{t('tg.usedNeeds')}</span>
              </span>
            ) : (
              <Button size="sm" variant="secondary" icon={<Send strokeWidth={1.5} />} onClick={onUseNeeds} data-testid="tg-use-needs">
                {t('tg.useNeeds')}
              </Button>
            )
          }
        >
          {t('tg.savedBody')}
        </Notice>
      )}
      <div className="st-actions">
        <Button
          ref={testRef}
          size="sm"
          variant="secondary"
          loading={test.isPending}
          icon={<Send strokeWidth={1.5} />}
          onClick={() => void runTest()}
          data-testid="tg-test"
        >
          {t(test.isPending ? 'tg.testing' : 'tg.test')}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={test.isPending}
          icon={<Replace strokeWidth={1.5} />}
          onClick={() => setSheet('replace')}
        >
          {t('tg.replace')}
        </Button>
        <span className="ns-spacer" />
        <Button
          size="sm"
          variant="plain-destructive"
          disabled={test.isPending}
          icon={<Trash2 strokeWidth={1.5} />}
          onClick={() => setSheet('remove')}
          data-testid="tg-remove"
        >
          {t('tg.remove')}
        </Button>
      </div>
      <ReplaceSheet open={sheet === 'replace'} onOpenChange={(o) => setSheet(o ? 'replace' : null)} onSaved={saved} />
      <RemoveSheet
        open={sheet === 'remove'}
        onOpenChange={(o) => setSheet(o ? 'remove' : null)}
        onRemoved={() => {
          setJustSaved(false)
          setTested(null)
        }}
      />
    </div>
  )
}
