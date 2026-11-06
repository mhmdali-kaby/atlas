import { useMutation } from '@tanstack/react-query'
import { KeyRound, Trash2 } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'
import { Button, Sheet, SheetClose, TextField, toast } from '../../ui'
import { ConnectionForm, KeyHelp, TestFailure, type Connection, type TestOutcome } from '../onboarding'
import { useStoreConnection, type Failure } from './shared'

/* The Replace keys / Connect sheet (reuses onboarding's ConnectionForm) and the Remove sheet. */

export function KeysSheet({
  open,
  onOpenChange,
  connection,
  env = connection?.env ?? 'demo',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** null: connect a new key for `env`; otherwise replace this connection's keys. */
  connection: Connection | null
  env?: Connection['env']
}) {
  const { t } = useTranslation('settings')
  const [failure, setFailure] = useState<Failure | null>(null)
  const replace = connection !== null
  const live = env === 'live'
  // A live key saves under its own copy and ids; the form, fields and flow are the demo one's.
  const title = live ? (replace ? t('lv.rk.title') : t('lv.ck.title')) : replace ? t('rk.title') : t('ck.title')
  const text = live ? (replace ? t('lv.rk.text') : t('lv.ck.text')) : replace ? t('rk.text') : t('ck.text')

  function onResult(outcome: TestOutcome) {
    if (outcome.status === 'failed') {
      setFailure({ error: outcome.error, okxCode: outcome.okxCode })
      return
    }
    onOpenChange(false)
    const last4 = outcome.connection.api_key_last4
    toast(
      live
        ? { tone: 'success', title: t('lv.savedToast'), body: t('lv.savedToastBody', { last4 }) }
        : { tone: 'success', title: replace ? t('rk.toast') : t('ck.toast'), body: t('rk.toastBody', { last4 }) },
    )
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      tone={live ? 'live' : 'default'}
      icon={<KeyRound strokeWidth={1.5} />}
      title={title}
      description={text}
    >
      {(live || !replace) && <KeyHelp env={env} />}
      {failure && <TestFailure error={failure.error} okxCode={failure.okxCode} env={env} />}
      <div className="st-sheet-fields">
        <ConnectionForm
          connection={connection}
          env={env}
          idPrefix={(live ? 'l' : '') + (replace ? 'rk' : 'ck')}
          submitLabel={t('rk.save')}
          cancel={
            <SheetClose>
              <Button variant="secondary">{t('cancel')}</Button>
            </SheetClose>
          }
          onTestStart={() => setFailure(null)}
          onResult={onResult}
        />
      </div>
    </Sheet>
  )
}

export function RemoveSheet({
  open,
  onOpenChange,
  connection,
  onRemoved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  connection: Connection
  onRemoved: () => void
}) {
  const { t } = useTranslation('settings')
  const store = useStoreConnection()
  const [typed, setTyped] = useState('')
  const formId = useId()
  const live = connection.env === 'live'
  const rm = live ? 'lv.rm' : 'rm'
  const remove = useMutation({
    mutationFn: () => api.delete<null>(`/api/connections/${connection.id}`),
    onSuccess: () => {
      store.drop(connection.id)
      onOpenChange(false)
      toast({ tone: 'success', title: t(`${rm}.toast`), body: t(`${rm}.toastBody`) })
      onRemoved()
    },
  })
  const matches = typed.trim() === connection.label

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (matches && !remove.isPending) remove.mutate()
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !remove.isPending && onOpenChange(next)}
      tone="destructive"
      icon={<Trash2 strokeWidth={1.5} />}
      title={t(`${rm}.title`)}
      description={t(`${rm}.text`)}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={remove.isPending}>
              {t('cancel')}
            </Button>
          </SheetClose>
          <Button type="submit" form={formId} variant="destructive" disabled={!matches} loading={remove.isPending}>
            {remove.isPending ? t('rm.removing') : t('rm.do')}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit}>
        <TextField
          id={live ? 'lrm-confirm' : 'rm-confirm'}
          label={t('rm.confirm', { label: connection.label })}
          placeholder={connection.label}
          dir="auto"
          autoComplete="off"
          spellCheck={false}
          value={typed}
          onChange={(e) => setTyped(e.currentTarget.value)}
          disabled={remove.isPending}
          error={remove.isError ? t('rm.failed') : undefined}
        />
      </form>
    </Sheet>
  )
}
