import { Clock, ExternalLink, Info, KeyRound, Replace, ServerCrash, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, SecureField, Sheet, SheetClose } from '../../ui'
import { Notice } from '../onboarding'
import { deleteKey, keyFailureOf, putKey, type KeyFailure } from './api'
import { monogram, PROVIDER_KEY_URLS, PROVIDER_SITES, providerName, type ApiSource, type KeyProvider } from './model'
import { Fig, NS } from './parts'

/*
 * ATL-69 (design §5.6): the Keys group, the key sheet (add or replace) and the remove sheet. A key
 * belongs to a provider; PUT/DELETE on any of its sources applies to all of them (ATL-71).
 *
 * The typed key lives only in the uncontrolled <input>: never in React state, a TanStack cache,
 * storage or a log. It is read once on submit and the field is emptied on success and on close.
 */

export function KeysGroup({
  providers,
  onAdd,
  onRemove,
}: {
  providers: KeyProvider[]
  onAdd: (provider: string, replace: boolean) => void
  onRemove: (provider: string) => void
}) {
  const { t } = useTranslation(NS)
  if (providers.length === 0) return null
  return (
    <section className="src-section" id="src-keys" aria-labelledby="src-keys-title" data-testid="src-keys">
      <div className="src-group-head">
        <h2 className="src-group-title" id="src-keys-title" tabIndex={-1}>
          {t('keys.title')}
        </h2>
      </div>
      <div className="src-group">
        {providers.map((p) => {
          const name = providerName(p.provider)
          return (
            <div className="src-row src-key-row" id={`src-key-${p.provider}`} key={p.provider} data-testid="src-key-row" data-provider={p.provider}>
              <span className="src-mono" aria-hidden="true">
                {monogram(p.sources[0] as Pick<ApiSource, 'provider' | 'name'>)}
              </span>
              <span className="src-main">
                <span className="src-name">
                  <bdi>{name}</bdi>
                </span>
                <span className="src-key-sub">{t(`keys.sub.${p.provider}`, { defaultValue: '' })}</span>
              </span>
              <span className="src-key-actions">
                {p.hasKey ? (
                  <>
                    <span className="src-key-mask" role="img" aria-label={t('keys.masked', { last4: p.last4 ?? '' })} data-testid="src-key-mask">
                      <Fig>{`•••• ${p.last4 ?? ''}`}</Fig>
                    </span>
                    <Button size="sm" variant="secondary" icon={<Replace strokeWidth={1.5} />} data-key-action="replace" onClick={() => onAdd(p.provider, true)}>
                      {t('keys.replace')}
                    </Button>
                    <Button size="sm" variant="plain-destructive" data-key-action="remove" onClick={() => onRemove(p.provider)}>
                      {t('keys.remove')}
                    </Button>
                  </>
                ) : (
                  <Button size="sm" variant="secondary" icon={<KeyRound strokeWidth={1.5} />} data-key-action="add" onClick={() => onAdd(p.provider, false)}>
                    {t('keys.add')}
                  </Button>
                )}
              </span>
            </div>
          )
        })}
      </div>
      <p className="src-group-note">{t('keys.note')}</p>
    </section>
  )
}

export interface KeyTarget {
  provider: string
  /** Any keyed source of the provider: the URL the PUT/DELETE goes to. */
  sourceKey: string
  replace: boolean
}

/*
 * Both sheets are mounted with a fresh `key` per opening (SourcesPage), so every opening starts
 * clean, and they keep their target while they animate out after `open` turns false.
 */

export function KeySheet({
  target,
  open,
  onOpenChange,
  onSaved,
  onCloseAutoFocus,
}: {
  target: KeyTarget
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (provider: string, saved: ApiSource) => void
  onCloseAutoFocus: (e: Event) => void
}) {
  const { t } = useTranslation(NS)
  const inputRef = useRef<HTMLInputElement>(null)
  const [filled, setFilled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<KeyFailure | null>(null)

  // Every close (Esc, scrim, Cancel, success) empties the field at once, before the exit animation.
  useEffect(() => {
    if (!open && inputRef.current) inputRef.current.value = ''
  }, [open])
  useEffect(() => {
    const input = inputRef.current
    return () => {
      if (input) input.value = ''
    }
  }, [])
  // A refused or throttled save hands focus back to the field once it is enabled again.
  useEffect(() => {
    if (failure && !busy) inputRef.current?.focus()
  }, [failure, busy])

  const shown = target
  const name = providerName(shown.provider)
  const site = PROVIDER_SITES[shown.provider]
  const url = PROVIDER_KEY_URLS[shown.provider]

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const input = inputRef.current
    if (!input || busy) return
    const value = input.value.trim()
    if (!value) return
    setBusy(true)
    setFailure(null)
    try {
      const res = await putKey(target.sourceKey, value)
      input.value = ''
      setFilled(false)
      onSaved(target.provider, res.data)
    } catch (err) {
      setFailure(keyFailureOf(err))
    } finally {
      setBusy(false)
    }
  }

  const formId = 'src-ks-form'
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !busy && onOpenChange(next)}
      onCloseAutoFocus={onCloseAutoFocus}
      onOpenAutoFocus={(e) => {
        // The sheet's one field takes focus (design §5.6), not the "Open <site>" link above it.
        e.preventDefault()
        inputRef.current?.focus()
      }}
      icon={<KeyRound strokeWidth={1.5} />}
      title={t(shown.replace ? 'ks.replaceTitle' : 'ks.addTitle', { provider: name })}
      description={t(`ks.text.${shown.provider}`, { defaultValue: '' }) || undefined}
      className="src-ks"
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={busy}>
              {t('ks.cancel')}
            </Button>
          </SheetClose>
          <Button type="submit" form={formId} variant="primary" disabled={!filled} loading={busy} data-testid="src-ks-save">
            {busy ? t('ks.saving') : t('ks.save')}
          </Button>
        </>
      }
    >
      <form id={formId} className="grid gap-4" noValidate onSubmit={(e) => void submit(e)} data-testid="src-ks">
        {(site || url) && (
          <Notice
            tone="info"
            icon={Info}
            title={t(`ks.where.${shown.provider}`, { defaultValue: '' })}
            actions={
              url ? (
                <Button asChild size="sm" variant="plain">
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink aria-hidden="true" strokeWidth={1.5} />
                    <span>{t('ks.open', { site })}</span>
                  </a>
                </Button>
              ) : undefined
            }
          />
        )}
        <SecureField
          id="src-ks-input"
          ref={inputRef}
          label={t('ks.field')}
          hint={t('ks.hint')}
          error={failure === 'invalid' ? t('ks.invalid', { provider: name }) : undefined}
          disabled={busy}
          name="src-api-key"
          autoComplete="off"
          data-1p-ignore=""
          data-lpignore="true"
          onChange={(e) => {
            setFilled(e.currentTarget.value.trim() !== '')
            if (failure === 'invalid') setFailure(null)
          }}
        />
        {(failure === 'limited' || failure === 'down') && (
          <Notice
            tone={failure === 'limited' ? 'warning' : 'error'}
            icon={failure === 'limited' ? Clock : ServerCrash}
            role="alert"
            title={t(failure === 'limited' ? 'ks.limited.title' : 'ks.down.title')}
            data-testid="src-ks-notice"
          >
            {t(failure === 'limited' ? 'ks.limited.body' : 'ks.down.body')}
          </Notice>
        )}
      </form>
    </Sheet>
  )
}

export function RemoveKeySheet({
  target,
  open,
  onOpenChange,
  onDone,
  onCloseAutoFocus,
}: {
  target: KeyTarget
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called once the DELETE answered: null on success, else how it failed. The sheet closes either way. */
  onDone: (provider: string, failure: KeyFailure | null) => void
  onCloseAutoFocus: (e: Event) => void
}) {
  const { t } = useTranslation(NS)
  const [busy, setBusy] = useState(false)
  const shown = target
  const name = providerName(shown.provider)

  async function remove() {
    if (busy) return
    setBusy(true)
    let failure: KeyFailure | null = null
    try {
      await deleteKey(target.sourceKey)
    } catch (err) {
      failure = keyFailureOf(err)
    }
    setBusy(false)
    onDone(target.provider, failure)
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !busy && onOpenChange(next)}
      onCloseAutoFocus={onCloseAutoFocus}
      tone="destructive"
      icon={<Trash2 strokeWidth={1.5} />}
      title={t('rm.title', { provider: name })}
      description={`${t(`rm.text.${shown.provider}`, { defaultValue: '' })} ${t('rm.again')}`.trim()}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={busy} data-testid="src-rm-cancel">
              {t('ks.cancel')}
            </Button>
          </SheetClose>
          <Button variant="destructive" loading={busy} onClick={() => void remove()} data-testid="src-rm-do">
            {busy ? t('rm.removing') : t('rm.do')}
          </Button>
        </>
      }
    />
  )
}
