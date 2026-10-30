import { useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CirclePause, Minus, Plus, RotateCcw, TriangleAlert, UsersRound } from 'lucide-react'
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../lib/api'
import { formatFixed, type Lang } from '../../lib/format'
import { Button, SegmentedControl, Skeleton, Toggle, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice, useSettings, type SettingsDoc } from '../onboarding'
import { useLang } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import '../settings-connections/settings.css'
import { historyKey, useSettingsHistory } from '../settings-risk/model'
import '../settings-risk/risk.css'
import { Medallion } from '../warroom/Medallion'
import '../warroom/warroom.css'
import { AgentActivity } from './Activity'
import { DomainList } from './DomainList'
import {
  ALIAS,
  BOUNDS,
  changesFor,
  compact,
  countText,
  current,
  isChanged,
  key,
  listProblems,
  LISTS,
  numError,
  parseCount,
  pruned,
  ROSTER,
  savedFrom,
  serverErrors,
  stepped,
  TIERS,
  useAgentUsage,
  useSaveAgentSettings,
  withEdit,
  type AgentSlug,
  type Edits,
  type ListWhich,
  type NumField,
  type Saved,
  type ServerError,
  type Tier,
  type Value,
} from './model'
import './settings-agents.css'

/*
 * Settings → Agents (brief §14.7, decision 247 (7); design v9 js/pages/settings-agents.js):
 * a summary with the roster, one grouped inset section per agent (on/off, model tier, daily token
 * and turn budgets), Web access (allowed and blocked sites) and Activity. Changes are batched into
 * one versioned PATCH /api/settings of flat dotted keys (ATL-113); a 409 takes the newer version
 * and keeps the owner's edits on top of it.
 */

const ENV: SettingsDoc['env'] = 'demo'

function PaneHeader({ version }: { version: number | null }) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <UsersRound strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
      {version !== null && (
        <span className="st-version" data-testid="settings-version">
          {t('version', { v: formatFixed(version, 0, lang) })}
        </span>
      )}
    </header>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings-agents')
  const parts = t('error.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="agents-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('error.title')}</h2>
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
          t('error.body')
        )}
      </p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('error.retry')}
      </Button>
    </section>
  )
}

function Loading() {
  return (
    <div className="grid gap-[22px]" aria-busy="true" data-testid="agents-loading">
      <div className="st-group sa-summary">
        <Skeleton className="h-[17px] w-[38%]" />
        <div className="flex justify-between">
          {ROSTER.map((s) => (
            <Skeleton key={s} className="size-8 rounded-full" />
          ))}
        </div>
      </div>
      {[0, 1, 2].map((i) => (
        <div key={i} className="st-group">
          {[0, 1, 2, 3].map((j) => (
            <div key={j} className="sa-row">
              <span className="sa-row-main gap-1.5">
                <Skeleton className="h-3.5 w-[30%]" />
                <Skeleton className="h-[11px] w-[46%]" />
              </span>
              <Skeleton className="h-[30px] w-[168px] rounded-[8px]" />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

const Fig = ({ children }: { children: string }) => (
  <bdi dir="ltr" className="tabular-nums">
    {children}
  </bdi>
)

/** "{{a}} text {{b}}" with React nodes in the placeholders (figures stay LTR inside Arabic). */
function fill(template: string, parts: Record<string, React.ReactNode>) {
  return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
    const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1]
    return <span key={i}>{name ? parts[name] : piece}</span>
  })
}

function Summary({ value, savedAt, onJump }: { value: (k: string) => Value; savedAt: string; onJump: (slug: AgentSlug) => void }) {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const lang = useLang()
  const on = ROSTER.filter((s) => value(key(s, 'enabled')) === true)
  const total = (f: NumField) => on.reduce((n, s) => n + (parseCount(value(key(s, f)) as string) ?? 0), 0)
  const raw = (k: string) => t(k, { on: '{{on}}', n: '{{n}}', tokens: '{{tokens}}', turns: '{{turns}}', date: '{{date}}' })
  return (
    <section className="sa-section" aria-label={t('nav')}>
      <div className="st-group sa-summary" data-testid="agents-summary">
        <div className="sa-sum-text">
          <p className="sa-sum-title">
            {on.length
              ? fill(raw('sum.on'), { on: <Fig>{countText(on.length, lang)}</Fig>, n: <Fig>{countText(ROSTER.length, lang)}</Fig> })
              : t('sum.allOff')}
          </p>
          <p className="sa-sum-sub">
            {on.length > 0 && (
              <>
                {fill(raw('sum.budget'), { tokens: <Fig>{compact(total('daily_tokens'), lang)}</Fig>, turns: <Fig>{countText(total('daily_turns'), lang)}</Fig> })}{' '}
              </>
            )}
            {fill(raw('sum.saved'), { date: <span className="tabular-nums">{formatDayTime(savedAt, lang)}</span> })}
          </p>
        </div>
        <ul className="sa-roster">
          {ROSTER.map((s) => {
            const off = value(key(s, 'enabled')) !== true
            const name = tw(`agents.${s}.name`)
            return (
              <li key={s}>
                <button
                  type="button"
                  className={cx('sa-jump', off && 'is-off')}
                  aria-label={off ? t('jumpOff', { name }) : t('jump', { name })}
                  onClick={() => onJump(s)}
                >
                  <Medallion slug={s} size={32} status={off ? 'offline' : 'idle'} />
                  <span className="sa-jump-name">{s === 'derivatives_sentiment' ? tw(`agents.${s}.short`) : name}</span>
                  {off && <span className="sa-jump-off">{t('off')}</span>}
                </button>
              </li>
            )
          })}
        </ul>
      </div>
      <p className="sa-group-note">{t('sum.models')}</p>
    </section>
  )
}

function usedLine(used: number | undefined, budgetText: string, f: NumField, lang: Lang) {
  if (used === undefined) return null
  if (!used) return { kind: 'none' as const }
  const v = f === 'daily_tokens' ? compact(used, lang) : countText(used, lang)
  const budget = parseCount(budgetText)
  if (budget !== null && budget > 0 && used >= budget) return { kind: 'over' as const, v }
  const pct = budget !== null && budget > 0 ? Math.round((used / budget) * 100) : null
  return { kind: 'used' as const, v, pct, warn: pct !== null && pct >= 80 }
}

function NumRow({
  slug,
  field,
  text,
  changed,
  used,
  serverError,
  onText,
  onStep,
  onBlur,
}: {
  slug: AgentSlug
  field: NumField
  text: string
  changed: boolean
  used: number | undefined
  serverError: string | undefined
  onText: (text: string) => void
  onStep: (dir: 1 | -1) => void
  onBlur: () => void
}) {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const lang = useLang()
  const label = t(field === 'daily_tokens' ? 'tokens' : 'turns')
  const name = tw(`agents.${slug}.name`)
  const b = BOUNDS[field]
  const v = parseCount(text)
  const problem = numError(key(slug, field), text)
  const error =
    problem === 'empty'
      ? t('err.empty', { label })
      : problem === 'range'
        ? t('err.range', { label, min: `⁦${countText(b.min, lang)}⁩`, max: `⁦${countText(b.max, lang)}⁩` })
        : serverError
  const u = usedLine(used, text, field, lang)
  const id = `sa-${slug}-${field}`
  const warn = u?.kind === 'over' || (u?.kind === 'used' && u.warn)
  return (
    <div className={cx('sa-row sa-num', changed && 'is-changed')} data-key={key(slug, field)}>
      <div className="sa-row-main">
        <label className="sa-row-label" htmlFor={id}>
          {label}
          {changed && <span className="sr-only">, {t('changed')}</span>}
        </label>
        {u && (
          <span id={`${id}-used`} className={cx('sa-row-sub sa-used', warn && 'is-warn')}>
            {warn && <TriangleAlert aria-hidden="true" strokeWidth={2} />}
            <span>
              {u.kind === 'none'
                ? t('notUsed')
                : u.kind === 'over'
                  ? fill(t('over', { v: '{{v}}', name }), { v: <Fig>{u.v}</Fig> })
                  : u.pct === null
                    ? fill(t('used', { v: '{{v}}' }), { v: <Fig>{u.v}</Fig> })
                    : fill(t('usedPct', { v: '{{v}}', p: '{{p}}' }), { v: <Fig>{u.v}</Fig>, p: <Fig>{`${u.pct}%`}</Fig> })}
            </span>
          </span>
        )}
        {error && (
          <p id={`${id}-err`} className="st-field-error text-footnote" data-testid={`error-${slug}-${field}`}>
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{error}</span>
          </p>
        )}
      </div>
      <div className={cx('st-stepper sa-stepper', field === 'daily_tokens' ? 'is-tokens' : 'is-turns', error && 'is-error')}>
        <div className="st-stepper-field" dir="ltr">
          <input
            id={id}
            className="st-stepper-input no-unit"
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            value={text}
            aria-invalid={error ? true : undefined}
            aria-describedby={[u && `${id}-used`, error && `${id}-err`].filter(Boolean).join(' ') || undefined}
            onChange={(e) => onText(e.target.value)}
            onBlur={onBlur}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault()
                onStep(e.key === 'ArrowUp' ? 1 : -1)
              }
            }}
          />
        </div>
        <div className="st-stepper-buttons">
          <button type="button" aria-label={t('dec', { label, name })} disabled={v !== null && v <= b.min} onClick={() => onStep(-1)}>
            <Minus aria-hidden="true" strokeWidth={2} />
          </button>
          <span className="st-stepper-sep" aria-hidden="true" />
          <button type="button" aria-label={t('inc', { label, name })} disabled={v !== null && v >= b.max} onClick={() => onStep(1)}>
            <Plus aria-hidden="true" strokeWidth={2} />
          </button>
        </div>
      </div>
    </div>
  )
}

/** "1010": which of enabled, model, daily_tokens, daily_turns differ from the saved values. */
function changedMask(saved: Saved, edits: Edits, slug: string): string {
  return (['enabled', 'model', 'daily_tokens', 'daily_turns'] as const).map((f) => (isChanged(saved, edits, key(slug, f)) ? '1' : '0')).join('')
}

const AgentSection = memo(function AgentSection({
  slug,
  on,
  tier,
  tokens,
  turns,
  changed,
  usedTokens,
  usedTurns,
  errTokens,
  errTurns,
  flash,
  onUpdate,
  onFlashEnd,
}: {
  slug: AgentSlug
  on: boolean
  tier: Tier
  tokens: string
  turns: string
  changed: string
  usedTokens: number | undefined
  usedTurns: number | undefined
  errTokens: string | undefined
  errTurns: string | undefined
  flash: boolean
  onUpdate: (k: string, v: Value) => void
  onFlashEnd: () => void
}) {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const lang = useLang()
  const name = tw(`agents.${slug}.name`)
  const kOn = key(slug, 'enabled')
  const kModel = key(slug, 'model')
  const [chOn, chModel, chTokens, chTurns] = changed.split('').map((c) => c === '1')
  const nums = [
    { f: 'daily_tokens' as const, text: tokens, ch: chTokens, used: usedTokens, err: errTokens },
    { f: 'daily_turns' as const, text: turns, ch: chTurns, used: usedTurns, err: errTurns },
  ]
  return (
    <section
      id={`sa-g-${slug}`}
      className={cx('sa-section sa-agent', !on && 'is-off', flash && 'is-flash')}
      aria-labelledby={`sa-n-${slug}`}
      data-testid={`agent-${slug}`}
      onAnimationEnd={onFlashEnd}
    >
      <div className="st-group">
        <div className={cx('sa-row sa-head', chOn && 'is-changed')}>
          <Medallion slug={slug} size={28} status={on ? 'idle' : 'offline'} />
          <span className="sa-row-main">
            <span className="sa-name" id={`sa-n-${slug}`}>
              {name}
              {chOn && <span className="sr-only">, {t('changed')}</span>}
            </span>
            <span className="sa-row-sub">{tw(`agents.${slug}.role`)}</span>
          </span>
          <Toggle checked={on} aria-label={t('use', { name })} onCheckedChange={(checked) => onUpdate(kOn, checked)} />
        </div>
        <div className={cx('sa-row sa-model', chModel && 'is-changed')} data-key={kModel}>
          <span className="sa-row-main">
            <span className="sa-row-label">
              {t('model')}
              {chModel && <span className="sr-only">, {t('changed')}</span>}
            </span>
            <span className="sa-row-sub sa-alias">
              <bdi dir="ltr">{t('alias', { alias: ALIAS[tier] ?? tier })}</bdi>
            </span>
          </span>
          <SegmentedControl<Tier>
            aria-label={t('modelOf', { name })}
            options={TIERS.map((x) => ({ value: x, label: t(`m.${x}`) }))}
            value={tier}
            onValueChange={(next) => onUpdate(kModel, next)}
          />
        </div>
        {nums.map(({ f, text, ch, used, err }) => {
          const k = key(slug, f)
          return (
            <NumRow
              key={f}
              slug={slug}
              field={f}
              text={text}
              changed={ch === true}
              used={used}
              serverError={err}
              onText={(next) => onUpdate(k, next)}
              onStep={(dir) => onUpdate(k, countText(stepped(k, text, dir), lang))}
              onBlur={() => {
                const n = parseCount(text)
                if (n !== null && numError(k, text) === null && countText(n, lang) !== text) onUpdate(k, countText(n, lang))
              }}
            />
          )
        })}
      </div>
      {!on && (
        <p className="sa-group-note sa-offnote">
          <CirclePause aria-hidden="true" strokeWidth={1.75} />
          <span>{t('offNote', { name })}</span>
        </p>
      )}
    </section>
  )
})

export function AgentsSettingsPage() {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const client = useQueryClient()
  const query = useSettings(ENV)
  const history = useSettingsHistory(ENV)
  const usage = useAgentUsage(ENV)
  const save = useSaveAgentSettings(ENV)
  const doc = query.data?.data

  const [edits, setEdits] = useState<Edits>({})
  const [errors, setErrors] = useState<Record<string, ServerError>>({})
  const [merged, setMerged] = useState<number | null>(null)
  const [elsewhere, setElsewhere] = useState<ReadonlySet<number>>(new Set())
  const [flash, setFlash] = useState<AgentSlug | null>(null)
  const [live, setLive] = useState('')
  const pageRef = useRef<HTMLDivElement>(null)
  const focusAfterSave = useRef<'invalid' | null>(null)

  useEffect(() => {
    if (focusAfterSave.current !== 'invalid') return
    focusAfterSave.current = null
    const el = pageRef.current?.querySelector<HTMLElement>('.sa-chip-x[aria-invalid="true"], input[aria-invalid="true"]')
    el?.scrollIntoView({ block: 'center' })
    el?.focus({ preventScroll: true })
  })

  /* Stable callbacks, so a group whose fields did not change does not re-render: a re-render of a
     segmented control that moved (an Off note appeared above it) would slide its thumb. */
  const savedRef = useRef<Saved | null>(null)
  const onUpdate = useCallback((k: string, v: Value) => {
    const s = savedRef.current
    if (!s) return
    setEdits((e) => withEdit(s, e, k, v))
    setErrors((errs) => {
      if (!(k in errs)) return errs
      const { [k]: _gone, ...rest } = errs
      return rest
    })
  }, [])
  const onFlashEnd = useCallback(() => setFlash(null), [])
  useLayoutEffect(() => {
    savedRef.current = doc ? savedFrom(doc) : null
  }, [doc])

  const announce = (text: string) => {
    setLive('')
    window.setTimeout(() => setLive(text), 30)
  }

  if (query.isPending || !doc) {
    return (
      <div ref={pageRef} className="st-page sa-page">
        <PaneHeader version={null} />
        {query.isPending ? <Loading /> : <LoadError onRetry={() => void query.refetch()} />}
      </div>
    )
  }

  const saved: Saved = savedFrom(doc)
  const value = (k: string) => current(saved, edits, k, lang)
  const changedCount = Object.keys(pruned(saved, edits)).length

  const update = onUpdate

  const numKeys = ROSTER.flatMap((s) => [key(s, 'daily_tokens'), key(s, 'daily_turns')])
  const listState = (which: ListWhich) => {
    const list = value(LISTS[which]) as string[]
    return { list, ...listProblems(errors, which, list) }
  }
  const lists = { allow: listState('allow'), deny: listState('deny') }
  const blocking =
    numKeys.filter((k) => numError(k, value(k) as string) !== null || errors[k]).length + (['allow', 'deny'] as const).filter((w) => lists[w].message !== null).length

  const usedOf = (slug: string, f: NumField) => {
    const row = usage.data?.data.find((a) => a.slug === slug)
    return row ? (f === 'daily_tokens' ? row.today.tokens : row.today.turns) : undefined
  }

  const onJump = (slug: AgentSlug) => {
    const g = document.getElementById(`sa-g-${slug}`)
    if (!g) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    g.scrollIntoView?.({ block: 'start', behavior: reduce ? 'auto' : 'smooth' })
    setFlash(null)
    window.requestAnimationFrame(() => setFlash(slug))
    g.querySelector<HTMLElement>('[role="switch"]')?.focus({ preventScroll: true })
  }

  const onSave = () => {
    if (save.isPending || blocking > 0 || changedCount === 0) return
    const changes = changesFor(saved, edits)
    save.mutate(
      { version: doc.version, changes },
      {
        onSuccess: (next) => {
          setEdits({})
          setErrors({})
          setMerged(null)
          toast({ tone: 'success', title: t('saved', { v: formatFixed(next.version, 0, lang) }), body: t('savedBody') })
        },
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            const fresh = await query.refetch()
            void client.invalidateQueries({ queryKey: historyKey(ENV) })
            const freshDoc = fresh.data?.data
            if (freshDoc) {
              const nextSaved = savedFrom(freshDoc)
              setEdits((e) => pruned(nextSaved, e))
              setMerged(freshDoc.version)
              setElsewhere((s) => new Set(s).add(freshDoc.version))
            }
            setErrors({})
            return
          }
          const found = serverErrors(err, changes)
          if (Object.keys(found).length > 0) {
            setErrors(found)
            focusAfterSave.current = 'invalid'
            announce(t('invalid'))
            return
          }
          toast({ tone: 'error', title: t('failed'), body: t('failedBody') })
        },
      },
    )
  }

  const plural = (k: string, n: number) => t(k, { count: n, n: formatFixed(n, 0, lang) })

  return (
    <div ref={pageRef} className="st-page sa-page">
      <PaneHeader version={doc.version} />

      {merged !== null && (
        <Notice tone="info" icon={RotateCcw} role="status" title={t('merged.title', { v: formatFixed(merged, 0, lang) })} className="sa-merged" data-testid="merged-notice">
          {t('merged.body')}
        </Notice>
      )}

      <Summary value={value} savedAt={doc.updated_at} onJump={onJump} />

      {ROSTER.map((slug) => {
        const k = (f: 'enabled' | 'model' | NumField) => key(slug, f)
        return (
          <AgentSection
            key={slug}
            slug={slug}
            on={value(k('enabled')) === true}
            tier={value(k('model')) as Tier}
            tokens={value(k('daily_tokens')) as string}
            turns={value(k('daily_turns')) as string}
            changed={changedMask(saved, edits, slug)}
            usedTokens={usedOf(slug, 'daily_tokens')}
            usedTurns={usedOf(slug, 'daily_turns')}
            errTokens={errors[k('daily_tokens')]?.message}
            errTurns={errors[k('daily_turns')]?.message}
            flash={flash === slug}
            onUpdate={onUpdate}
            onFlashEnd={onFlashEnd}
          />
        )
      })}

      <section className="sa-section" aria-labelledby="sa-web-title" data-testid="web-access">
        <h2 className="sa-group-title" id="sa-web-title">
          {t('web')}
        </h2>
        <div className="st-group">
          <div className="sa-row sa-web-intro">
            <span className="sa-web-medals">
              <Medallion slug="market_scout" size={22} status={value(key('market_scout', 'enabled')) === true ? 'idle' : 'offline'} />
              <Medallion slug="news_intelligence" size={22} status={value(key('news_intelligence', 'enabled')) === true ? 'idle' : 'offline'} />
            </span>
            <span className="sa-row-main">
              <span className="sa-row-sub">{t('webIntro')}</span>
            </span>
          </div>
          {(['allow', 'deny'] as const).map((which) => (
            <DomainList
              key={which}
              which={which}
              list={lists[which].list}
              saved={saved[LISTS[which]] as string[]}
              bad={lists[which].bad}
              error={lists[which].message}
              changed={isChanged(saved, edits, LISTS[which])}
              onChange={(next) => update(LISTS[which], next)}
              announce={announce}
            />
          ))}
        </div>
      </section>

      <section className="sa-section" aria-labelledby="sa-act-title">
        <h2 className="sa-group-title" id="sa-act-title">
          {t('activity')}
        </h2>
        <AgentActivity entries={history.data?.data} loading={history.isPending} elsewhere={elsewhere} />
        <p className="sa-group-note">{t('activityNote')}</p>
      </section>

      {changedCount > 0 && (
        <div className={cx('st-save-bar sa-bar', blocking > 0 && 'is-blocked')} role="region" aria-label={plural('unsaved', changedCount)} data-testid="save-bar">
          <span className="st-save-dot" aria-hidden="true" />
          <span className="st-save-text" aria-live="polite">
            {blocking > 0 ? plural('fix', blocking) : plural('unsaved', changedCount)}
          </span>
          <Button
            variant="plain"
            size="sm"
            icon={<RotateCcw className="rtl:-scale-x-100" />}
            disabled={save.isPending}
            onClick={() => {
              setEdits({})
              setErrors({})
              setMerged(null)
            }}
          >
            {t('revert')}
          </Button>
          <Button variant="primary" size="sm" loading={save.isPending} disabled={blocking > 0} onClick={onSave}>
            {save.isPending ? t('saving') : t('save')}
          </Button>
        </div>
      )}
      <div className="sr-only" aria-live="polite" data-testid="sa-live">
        {live}
      </div>
    </div>
  )
}
