import { useQueryClient } from '@tanstack/react-query'
import { CircleCheck, CloudOff, ExternalLink, KeyRound, Replace, RefreshCw, Rss, TriangleAlert } from 'lucide-react'
import { useImperativeHandle, useRef, useState, type Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, GroupedList, Row, SecureField, Skeleton, toast, Toggle } from '../../ui'
import { cx } from '../../ui/cx'
import { keyFailureOf, putKey, SOURCES_KEY, useSources, useToggleSource, type KeyFailure } from '../settings-data-sources/api'
import { KeySheet, type KeyTarget } from '../settings-data-sources/KeySheets'
import {
  groups,
  keyProviders,
  monogram,
  PROVIDER_KEY_URLS,
  PROVIDER_SITES,
  providerName,
  withKey,
  type ApiSource,
  type SourcesReport,
} from '../settings-data-sources/model'
import { NS as SRC_NS, useSourceName } from '../settings-data-sources/parts'
import { Notice } from './Notice'
import { StepHead, StepMain, type StepProps } from './steps'
import './onboarding-n.css'

/** What the page's Continue calls: saves the typed keys; false keeps the owner on the step. */
export interface SourcesHandle {
  save: () => Promise<boolean>
}

/** A figure or brand name inside a sentence: an LTR island. */
const Fig = ({ children }: { children: string }) => (
  <bdi dir="ltr" className="fig">
    {children}
  </bdi>
)

/**
 * Data sources (optional), design onboarding.js sources(): the count of sources that will work,
 * the two optional keys (CryptoPanic, Finnhub) and every source with its switch, from the
 * Settings → Data sources API (GET /api/data-sources, PATCH {enabled}, PUT …/key).
 *
 * A typed key lives only in its uncontrolled field (ATL-69): the step keeps whether a field is
 * filled, never what is in it. Continue saves each filled field; Skip (the page's footer) leaves
 * without saving anything. A saved key shows only as its last 4, with Replace (the key sheet).
 */
export function SourcesStep({ autoFocus, ref, onBusyChange }: StepProps & { ref?: Ref<SourcesHandle>; onBusyChange?: (busy: boolean) => void }) {
  const { t } = useTranslation('onboarding')
  const { t: ts } = useTranslation(SRC_NS)
  const qc = useQueryClient()
  const query = useSources()
  const name = useSourceName()
  const inputs = useRef<Record<string, HTMLInputElement | null>>({})
  const [filled, setFilled] = useState<Record<string, boolean>>({})
  const [failed, setFailed] = useState<Record<string, KeyFailure | undefined>>({})
  const [sheet, setSheet] = useState<{ target: KeyTarget; open: boolean; n: number } | null>(null)
  const replaceBtn = useRef<HTMLElement | null>(null)

  const toggle = useToggleSource((vars) => {
    const s = qc.getQueryData<SourcesReport>(SOURCES_KEY)?.data.find((x) => x.key === vars.key)
    toast({ tone: 'error', title: t('sources.toggleFailed', { name: s ? name(s) : vars.key }), body: t('sources.toggleFailedBody') })
  })

  const applyKey = (provider: string, last4: string) => {
    const before = qc.getQueryData<SourcesReport>(SOURCES_KEY)
    if (before) qc.setQueryData<SourcesReport>(SOURCES_KEY, withKey(before, provider, last4, Date.now()))
    void qc.invalidateQueries({ queryKey: SOURCES_KEY })
  }

  useImperativeHandle(ref, () => ({
    save: async () => {
      const rows = qc.getQueryData<SourcesReport>(SOURCES_KEY)?.data ?? []
      const typed = keyProviders(rows).filter((p) => !p.hasKey && (inputs.current[p.provider]?.value.trim() ?? '') !== '')
      if (typed.length === 0) return true
      onBusyChange?.(true)
      const errors: Record<string, KeyFailure> = {}
      let saved = 0
      for (const p of typed) {
        const input = inputs.current[p.provider]
        const value = input?.value.trim() ?? ''
        try {
          const res = await putKey(p.sources[0]!.key, value)
          if (input) input.value = ''
          saved++
          applyKey(p.provider, res.data.key_last4 ?? '')
          setFilled((f) => ({ ...f, [p.provider]: false }))
        } catch (err) {
          errors[p.provider] = keyFailureOf(err)
        }
      }
      onBusyChange?.(false)
      setFailed(errors)
      if (saved > 0) toast({ tone: 'success', title: t('sources.savedToast', { count: saved }), body: t('sources.savedBody') })
      const first = Object.keys(errors)[0]
      if (first) {
        requestAnimationFrame(() => inputs.current[first]?.focus())
        return false
      }
      return true
    },
  }))

  const head = <StepHead autoFocus={autoFocus} title={t('sources.title')} lead={t('sources.lead')} />

  if (query.isPending) {
    return (
      <>
        {head}
        <StepMain>
          <div aria-busy="true" aria-label={t('sources.loading')} className="grid gap-5" data-testid="sources-loading">
            <Skeleton className="h-[68px] rounded-card" />
            <Skeleton className="h-[150px] rounded-card" />
            <Skeleton className="h-[220px] rounded-card" />
          </div>
        </StepMain>
      </>
    )
  }

  if (query.isError) {
    return (
      <>
        {head}
        <StepMain>
          <Notice
            tone="error"
            role="alert"
            icon={CloudOff}
            title={t('sources.error.title')}
            actions={
              <Button size="sm" icon={<RefreshCw />} onClick={() => void query.refetch()}>
                {t('retry')}
              </Button>
            }
          >
            {t('sources.error.body')}
          </Notice>
        </StepMain>
      </>
    )
  }

  const rows = query.data.data
  const providers = keyProviders(rows)
  const keyed = (provider: string) => {
    const p = providers.find((x) => x.provider === provider)
    return !!p && (p.hasKey || (!!filled[provider] && !failed[provider]))
  }
  const on = rows.filter((s) => s.enabled)
  const works = on.filter((s) => !s.requires_key || keyed(s.provider))
  const missing = [...new Set(on.filter((s) => s.requires_key && !keyed(s.provider)).map((s) => s.provider))].map(providerName)
  const anyKey = providers.some((p) => keyed(p.provider))
  const none = on.length === 0
  const ok = !none && missing.length === 0
  const status = (s: ApiSource) => {
    if (!s.enabled) return <span>{t('sources.st.off')}</span>
    if (!s.requires_key) return <span>{t('sources.st.free')}</span>
    const provider = providerName(s.provider)
    return keyed(s.provider) ? (
      <span className="ob-src-uses">
        <KeyRound aria-hidden="true" strokeWidth={1.75} />
        {t('sources.st.uses', { provider })}
      </span>
    ) : (
      <span className="ob-src-needs">{t('sources.st.needs', { provider })}</span>
    )
  }

  const SumIcon = none ? TriangleAlert : ok ? CircleCheck : Rss
  return (
    <>
      {head}
      <StepMain>
        <div className={cx('ob-src-sum', none && 'is-warn', ok && 'is-ok')} aria-live="polite" data-testid="sources-summary">
          <span className="ob-src-sum-icon" aria-hidden="true">
            <SumIcon strokeWidth={1.75} />
          </span>
          <div>
            <p className="ob-src-sum-title">
              {none
                ? t('sources.noneOn')
                : t(anyKey ? 'sources.works' : 'sources.noKey', { count: on.length, n: works.length })}
            </p>
            {!none && (
              <p className="ob-src-sum-sub">
                {missing.length >= 2
                  ? t('sources.need2', { a: missing[0], b: missing[1] })
                  : missing.length === 1
                    ? t('sources.need1', { a: missing[0] })
                    : t('sources.allSet')}
              </p>
            )}
          </div>
        </div>

        {providers.length > 0 && (
          <GroupedList title={t('sources.keys')} note={t('sources.keysNote')} className="ob-keys">
            {providers.map((p) => {
              const pname = providerName(p.provider)
              const site = PROVIDER_SITES[p.provider]
              const url = PROVIDER_KEY_URLS[p.provider]
              const fail = failed[p.provider]
              const fieldId = `ob-key-${p.provider}`
              return (
                <div role="listitem" className="ob-key-row" key={p.provider} data-provider={p.provider} data-testid="ob-key-row">
                  <div className="ob-key-head">
                    <span className="ob-mono-tile" aria-hidden="true">
                      {monogram(p.sources[0]!)}
                    </span>
                    <span className="ob-key-id">
                      <span className="ob-key-name">
                        <bdi>{pname}</bdi>
                      </span>
                      <span className="ob-key-meta">
                        <span>{t('sources.keyUsed', { count: p.sources.length })}</span>
                        {url && site && (
                          <a className="ob-key-link" href={url} target="_blank" rel="noopener noreferrer">
                            {t('sources.keyFree', { site })}
                            <ExternalLink aria-hidden="true" strokeWidth={1.75} />
                          </a>
                        )}
                      </span>
                    </span>
                  </div>
                  {p.hasKey ? (
                    <div className="ob-key-saved">
                      <span
                        className="ob-key-mask"
                        role="img"
                        aria-label={t('sources.savedKey', { last4: p.last4 ?? '' })}
                        data-testid="ob-key-mask"
                      >
                        <Fig>{`•••• ${p.last4 ?? ''}`}</Fig>
                      </span>
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<Replace strokeWidth={1.5} />}
                        onClick={(e) => {
                          replaceBtn.current = e.currentTarget
                          setSheet((prev) => ({ target: { provider: p.provider, sourceKey: p.sources[0]!.key, replace: true }, open: true, n: (prev?.n ?? 0) + 1 }))
                        }}
                      >
                        {t('sources.replace')}
                      </Button>
                    </div>
                  ) : (
                    <SecureField
                      id={fieldId}
                      ref={(el) => {
                        inputs.current[p.provider] = el
                      }}
                      label={t('sources.keyField', { provider: pname })}
                      placeholder={t('sources.keyPh')}
                      name={`ob-${p.provider}-key`}
                      autoComplete="off"
                      data-1p-ignore=""
                      data-lpignore="true"
                      error={
                        fail === 'invalid'
                          ? t('sources.invalid', { provider: pname, site: site ?? '' })
                          : fail === 'limited'
                            ? t('sources.limited')
                            : fail === 'down'
                              ? t('sources.down')
                              : undefined
                      }
                      onChange={(e) => {
                        const isFilled = e.currentTarget.value.trim() !== ''
                        setFilled((f) => (f[p.provider] === isFilled ? f : { ...f, [p.provider]: isFilled }))
                        if (fail) setFailed((f) => ({ ...f, [p.provider]: undefined }))
                      }}
                    />
                  )}
                </div>
              )
            })}
          </GroupedList>
        )}

        <div className="ob-src-list" role="group" aria-label={t('sources.list')}>
          {groups(rows).map((g) => (
            <GroupedList key={g.cat} title={ts(`cat.${g.cat}`)}>
              {g.rows.map((s) => {
                const labelId = `ob-src-${s.key.replace(/\./g, '-')}`
                const mono = monogram(s)
                return (
                  <Row
                    key={s.key}
                    className={cx('ob-src-row', !s.enabled && 'is-off')}
                    labelId={labelId}
                    icon={<span className={cx('ob-mono', mono.length > 2 && 'is-long')}>{mono}</span>}
                    label={name(s)}
                    sub={<span className="ob-src-st">{status(s)}</span>}
                  >
                    <Toggle
                      checked={s.enabled}
                      aria-labelledby={labelId}
                      onCheckedChange={(enabled) => toggle.mutate({ key: s.key, enabled })}
                    />
                  </Row>
                )
              })}
            </GroupedList>
          ))}
        </div>

        {sheet && (
          <KeySheet
            key={sheet.n}
            target={sheet.target}
            open={sheet.open}
            onOpenChange={(open) => !open && setSheet((s) => (s ? { ...s, open: false } : s))}
            onSaved={(provider, saved) => {
              applyKey(provider, saved.key_last4 ?? '')
              setSheet((s) => (s ? { ...s, open: false } : s))
              toast({ tone: 'success', title: t('sources.savedToast', { count: 1 }), body: t('sources.savedBody') })
            }}
            onCloseAutoFocus={(e) => {
              if (replaceBtn.current?.isConnected) {
                e.preventDefault()
                replaceBtn.current.focus()
              }
            }}
          />
        )}
      </StepMain>
    </>
  )
}
