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
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** null: connect a new demo key; otherwise replace this connection's keys. */
  connection: Connection | null
}) {
  const { t } = useTranslation('settings')
  const [failure, setFailure] = useState<Failure | null>(null)
  const replace = connection !== null

  function onResult(outcome: TestOutcome) {
    if (outcome.status === 'failed') {
      setFailure({ error: outcome.error, okxCode: outcome.okxCode })
      return
    }
    onOpenChange(false)
    toast({
      tone: 'success',
      title: replace ? t('rk.toast') : t('ck.toast'),
      body: t('rk.toastBody', { last4: outcome.connection.api_key_last4 }),
    })
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      icon={<KeyRound strokeWidth={1.5} />}
      title={replace ? t('rk.title') : t('ck.title')}
      description={replace ? t('rk.text') : t('ck.text')}
    >
      {!replace && <KeyHelp />}
      {failure && <TestFailure error={failure.error} okxCode={failure.okxCode} />}
      <div className="st-sheet-fields">
        <ConnectionForm
          connection={connection}
          idPrefix={replace ? 'rk' : 'ck'}
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
  const remove = useMutation({
    mutationFn: () => api.delete<null>(`/api/connections/${connection.id}`),
    onSuccess: () => {
      store.drop(connection.id)
      onOpenChange(false)
      toast({ tone: 'success', title: t('rm.toast'), body: t('rm.toastBody') })
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
      title={t('rm.title')}
      description={t('rm.text')}
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
          id="rm-confirm"
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
