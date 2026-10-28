import { useQueryClient } from '@tanstack/react-query'
import { CircleAlert, RotateCcw, Shield } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../lib/api'
import { formatFixed } from '../../lib/format'
import { Button, Skeleton, toast } from '../../ui'
import { useLang } from '../portfolio/hooks'
import {
  AutonomyPicker,
  CompareDisclosure,
  enabledAutonomyLevels,
  LimitsCompare,
  LIMIT_DEFS,
  Notice,
  RiskProfilePicker,
  useSettings,
  type RiskLimits,
  type RiskProfile,
} from '../onboarding'
import '../settings-connections/settings.css'
import { ActivityList } from './Activity'
import { LimitRow } from './LimitRow'
import {
  changesFor,
  draftFrom,
  emptyFields,
  fieldErrors,
  historyKey,
  inputText,
  isDirty,
  parseLimit,
  presetDraft,
  stepped,
  useSaveSettings,
  useSettingsHistory,
  withLimit,
  type Draft,
  type LimitField,
} from './model'
import './risk.css'

/*
 * Settings → Risk & Autonomy (brief §14.3, §14.6; design js/pages/settings.js `risk`):
 * profile cards, the ten limits as steppers, autonomy 1–2, and the Activity list of versions.
 * Nothing is saved until Save changes; a 409 reloads the current values and says so.
 */

function PaneHeader({ version }: { version: number | null }) {
  const { t } = useTranslation('settings')
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Shield strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav.risk')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('risk.intro')}</p>
      </div>
      {version !== null && (
        <span className="st-version" data-testid="settings-version">
          {t('risk.version', { v: formatFixed(version, 0, lang) })}
        </span>
      )}
    </header>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings')
  const parts = t('risk.error.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="risk-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('risk.error.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code dir="ltr" className="rounded-[6px] bg-fill-3 px-1.5 text-label [unicode-bidi:isolate]">
              ./atlas up
            </code>
            {parts[1]}
          </>
        ) : (
          t('risk.error.body')
        )}
      </p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('risk.error.retry')}
      </Button>
    </section>
  )
}

function Loading() {
  return (
    <div className="grid gap-7" aria-busy="true" data-testid="risk-loading">
      <div className="ob-profile-cards has-custom">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[112px] rounded-card" />
        ))}
      </div>
      <div className="st-group">
        {LIMIT_DEFS.map((d) => (
          <div key={d.field} className="st-limit-row">
            <Skeleton className="h-3.5 w-[44%]" />
            <Skeleton className="h-[30px] w-[164px] rounded-[8px]" />
          </div>
        ))}
      </div>
    </div>
  )
}

function Section({ id, title, note, children }: { id: string; title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="grid" aria-labelledby={id}>
      <h2 id={id} className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
        {title}
      </h2>
      {children}
      {note && <p className="px-4 pt-2 text-footnote text-label-secondary">{note}</p>}
    </section>
  )
}

export function RiskPage() {
  const { t } = useTranslation('settings')
  const { t: tOb } = useTranslation('onboarding')
  const lang = useLang()
  const client = useQueryClient()
  const query = useSettings('demo')
  const history = useSettingsHistory('demo')
  const save = useSaveSettings('demo')
  const doc = query.data?.data

  /** `null` = no edits: the page shows the saved settings as they are. */
  const [edits, setEdits] = useState<Draft | null>(null)
  const [serverErrors, setServerErrors] = useState<Partial<Record<string, string>>>({})
  const [conflictVersion, setConflictVersion] = useState<number | null>(null)
  const [elsewhere, setElsewhere] = useState<ReadonlySet<number>>(new Set())
  const pageRef = useRef<HTMLDivElement>(null)
  const focusError = useRef(false)

  useEffect(() => {
    if (!focusError.current) return
    focusError.current = false
    pageRef.current?.querySelector<HTMLInputElement>('input[aria-invalid="true"]')?.focus()
  })

  const draft = edits ?? (doc ? draftFrom(doc) : null)

  const update = (next: Draft, field?: LimitField) => {
    setEdits(next)
    if (field && serverErrors[field]) setServerErrors(({ [field]: _gone, ...rest }) => rest)
  }

  const onProfile = (profile: RiskProfile) => {
    if (!doc || !draft) return
    setServerErrors({})
    update(profile === 'custom' ? { ...draft, profile } : presetDraft(draft, profile, doc.presets))
  }

  const onSave = () => {
    if (!doc || !draft || save.isPending) return
    const changes = changesFor(draft, doc)
    save.mutate(
      { version: doc.version, changes },
      {
        onSuccess: (saved) => {
          setEdits(null)
          setServerErrors({})
          setConflictVersion(null)
          toast({ tone: 'success', title: t('risk.saved', { v: formatFixed(saved.version, 0, lang) }), body: t('risk.savedBody') })
        },
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            const fresh = await query.refetch()
            void client.invalidateQueries({ queryKey: historyKey('demo') })
            const v = fresh.data?.data.version ?? (err.body as { current_version?: number } | null)?.current_version ?? null
            setEdits(null)
            setServerErrors({})
            setConflictVersion(v)
            if (v !== null) setElsewhere((s) => new Set(s).add(v))
            return
          }
          const errors = fieldErrors(err)
          if (Object.keys(errors).length > 0) {
            setServerErrors(errors)
            focusError.current = true
            toast({ tone: 'error', title: t('risk.saveError.title'), body: t('risk.invalid') })
            return
          }
          toast({ tone: 'error', title: t('risk.saveError.title'), body: t('risk.saveError.body') })
        },
      },
    )
  }

  let body
  if (query.isPending) {
    body = <Loading />
  } else if (!doc || !draft) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else {
    const dirty = isDirty(draft, doc)
    const blocked = emptyFields(draft).length > 0
    // The Custom card's figures are hidden on this page (design: the limits list carries them).
    const customValues = draft.values as unknown as RiskLimits
    body = (
      <>
        {conflictVersion !== null && (
          <Notice tone="warning" icon={RotateCcw} role="status" title={t('risk.conflict.title')} data-testid="conflict-notice">
            {t('risk.conflict.body', { v: formatFixed(conflictVersion, 0, lang) })}
          </Notice>
        )}

        <Section id="st-profile-title" title={tOb('rp.label')}>
          <RiskProfilePicker value={draft.profile} onChange={onProfile} presets={doc.presets} customValues={customValues} />
          <div className="mt-2.5">
            <CompareDisclosure>
              <LimitsCompare presets={doc.presets} selected={draft.profile} />
            </CompareDisclosure>
          </div>
        </Section>

        <Section id="st-limits-title" title={t('risk.limits')} note={t('risk.limitsNote')}>
          <Tooltip.Provider delayDuration={450}>
            <div className="st-group" data-testid="limits">
              {LIMIT_DEFS.map((def) => (
                <LimitRow
                  key={def.field}
                  def={def}
                  text={draft.values[def.field]}
                  base={draft.base}
                  presets={doc.presets}
                  serverError={serverErrors[def.field]}
                  onChange={(text) => update(withLimit(draft, def.field, text, doc.presets), def.field)}
                  onStep={(dir) => update(withLimit(draft, def.field, stepped(def.field, draft.values[def.field], dir), doc.presets), def.field)}
                  onBlur={() => {
                    const text = draft.values[def.field]
                    if (parseLimit(text) !== null && !serverErrors[def.field]) {
                      const tidy = inputText(def.field, text.replace(/[,٫]/, '.'))
                      if (tidy !== text) setEdits({ ...draft, values: { ...draft.values, [def.field]: tidy } })
                    }
                  }}
                />
              ))}
            </div>
          </Tooltip.Provider>
        </Section>

        <Section id="st-au-title" title={tOb('au.label')} note={t('risk.autonomyNote')}>
          <AutonomyPicker value={draft.autonomy} enabled={enabledAutonomyLevels(doc)} onChange={(level) => update({ ...draft, autonomy: level })} />
        </Section>

        <Section id="st-act-title" title={t('risk.activity')} note={t('risk.activityNote')}>
          <ActivityList entries={history.data?.data} loading={history.isPending} elsewhere={elsewhere} />
        </Section>

        {dirty && (
          <div className="st-save-bar" role="region" aria-label={t('risk.unsaved')} data-testid="save-bar">
            <span className="st-save-dot" aria-hidden="true" />
            <span className="st-save-text" aria-live="polite">
              {t('risk.unsaved')}
            </span>
            <Button
              variant="plain"
              size="sm"
              icon={<RotateCcw className="rtl:-scale-x-100" />}
              disabled={save.isPending}
              onClick={() => {
                setEdits(null)
                setServerErrors({})
              }}
            >
              {t('risk.revert')}
            </Button>
            <Button variant="primary" size="sm" loading={save.isPending} disabled={blocked} onClick={onSave}>
              {save.isPending ? t('risk.saving') : t('risk.save')}
            </Button>
          </div>
        )}
      </>
    )
  }

  return (
    <div ref={pageRef} className="st-page st-risk">
      <PaneHeader version={doc && !query.isPending ? doc.version : null} />
      {body}
    </div>
  )
}
