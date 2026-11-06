import { ChevronLeft, ChevronsUpDown, CircleAlert, CircleCheck, Copy, FileClock, Info, Pin, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router'
import { formatFixed } from '../../lib/format'
import { Button, SegmentedControl, Skeleton, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import '../settings-connections/settings.css'
import { useSettingsHistory } from '../settings-risk/model'
import { Medallion } from '../warroom/Medallion'
import { ROSTER, type AgentSlug } from './model'
import { PromptDiff, DiffCounts, PromptReader } from './PromptText'
import { linesOf, pinErrorText, pinnedAt, usage, usePinPrompt, usePromptBody, usePromptVersions, type PromptList, type PromptVersion } from './prompts'
import { UsePromptSheet } from './UsePromptSheet'
import './prompts.css'

/*
 * Settings → Agents → Prompt history (brief §14.7, decision 265 (3); design v22 §7,
 * js/pages/settings-prompts.js). Full width, reached from each agent's Prompt row:
 * /settings/agents/prompts?agent=historian&v=2&mode=compare&from=1. A version rail (in use,
 * prompt file, runs per version) beside the text; Read or Compare (a jsdiff line diff); and
 * "Use version N…" / "Use the prompt file again", which call PUT prompt-pin.
 */

const isSlug = (s: string | null): s is AgentSlug => s !== null && (ROSTER as readonly string[]).includes(s)
const num = (s: string | null) => (s !== null && /^\d+$/.test(s) ? Number(s) : null)

function Head({ slug, onAgent }: { slug: AgentSlug; onAgent: (s: AgentSlug) => void }) {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const name = tw(`agents.${slug}.name`)
  return (
    <>
      <Button asChild variant="plain" size="sm" className="pmt-back">
        <Link to="/settings/agents">
          <ChevronLeft aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
          <span>{t('pr.back')}</span>
        </Link>
      </Button>
      <header className="pmt-head">
        <div className="pmt-head-main">
          <Medallion slug={slug} size={44} status="idle" />
          <div className="pmt-head-text">
            <h2 className="text-title-2">{t('pr.head', { name })}</h2>
            <p className="text-subheadline text-label-secondary">{t('pr.intro')}</p>
          </div>
        </div>
        <label className="pmt-pick">
          <span className="sr-only">{t('pr.agent')}</span>
          <Medallion slug={slug} size={22} status="idle" />
          <select value={slug} onChange={(e) => isSlug(e.target.value) && onAgent(e.target.value)} data-testid="agent-pick">
            {ROSTER.map((s) => (
              <option key={s} value={s}>
                {tw(`agents.${s}.name`)}
              </option>
            ))}
          </select>
          <ChevronsUpDown className="pmt-pick-caret" aria-hidden="true" strokeWidth={2} />
        </label>
      </header>
    </>
  )
}

function Rail({ list, sel, onSelect }: { list: PromptList; sel: number; onSelect: (v: number) => void }) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const u = usage(list)
  return (
    <nav className="pmt-rail" aria-label={t('pr.versions')} data-testid="prompt-rail">
      <ol>
        {list.data.map((v) => {
          const inUse = v.version === u.inUse
          return (
            <li key={v.version} className={cx('pmt-node', inUse && 'is-use', v.version === sel && 'is-sel')}>
              <button
                type="button"
                className="pmt-node-btn"
                aria-current={v.version === sel ? 'true' : undefined}
                onClick={() => onSelect(v.version)}
                data-testid={`rail-v${v.version}`}
              >
                <span className="pmt-dot" aria-hidden="true" />
                <span className="pmt-node-main">
                  <span className="pmt-node-top">
                    <span className="pmt-node-name">{t('pr.v', { n: v.version })}</span>
                    {inUse && (
                      <span className="pmt-badge is-use">
                        {u.pinned !== null ? <Pin aria-hidden="true" /> : <CircleCheck aria-hidden="true" />}
                        {t(u.pinned !== null ? 'pr.pinned' : 'pr.inUse')}
                      </span>
                    )}
                    {v.is_current_file && <span className="pmt-badge is-file">{t('pr.file')}</span>}
                  </span>
                  <span className="pmt-node-meta">
                    <span className="tabular-nums">{formatDayTime(v.created_at, lang)}</span>
                    <code dir="ltr">{v.hash12}</code>
                  </span>
                  <span className="pmt-node-delta">
                    {v.runs_count ? t('pr.runs', { count: v.runs_count, n: formatFixed(v.runs_count, 0, lang) }) : t('pr.runsNone')}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

function Status({ slug, name, list, onUnpin, unpinning }: { slug: AgentSlug; name: string; list: PromptList; onUnpin: () => void; unpinning: boolean }) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const u = usage(list)
  const history = useSettingsHistory(list.meta.env)
  if (u.pinned === null)
    return (
      <div className="pmt-status" role="status" data-testid="prompt-status">
        <CircleCheck aria-hidden="true" strokeWidth={2} />
        <span>{u.file !== null ? t('pr.usingFile', { name, n: u.file }) : t('pr.usingNewest', { name })}</span>
      </div>
    )
  const at = pinnedAt(history.data?.data, slug, u.pinned)
  const n = u.file ?? '—'
  return (
    <div className="pmt-status is-pinned" role="status" data-testid="prompt-status">
      <Pin aria-hidden="true" strokeWidth={2} />
      <span>{at ? t('pr.pinnedNote', { name, v: u.pinned, n, date: formatDayTime(at, lang) }) : t('pr.pinnedNoteNoDate', { name, v: u.pinned, n })}</span>
      <Button variant="secondary" size="sm" loading={unpinning} onClick={onUnpin} data-testid="unpin">
        {t('pr.useFile')}
      </Button>
    </div>
  )
}

function TextSkeleton() {
  return (
    <div className="pmt-text-sk" aria-hidden="true">
      {Array.from({ length: 10 }, (_, i) => (
        <Skeleton key={i} className="h-3" style={{ width: `${60 + ((i * 17) % 38)}%` }} />
      ))}
    </div>
  )
}

function View({
  slug,
  list,
  sel,
  mode,
  from,
  onMode,
  onFrom,
  onUse,
  onUnpin,
  unpinning,
}: {
  slug: AgentSlug
  list: PromptList
  sel: PromptVersion
  mode: 'read' | 'compare'
  from: number | null
  onMode: (m: 'read' | 'compare') => void
  onFrom: (v: number) => void
  onUse: () => void
  onUnpin: () => void
  unpinning: boolean
}) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const u = usage(list)
  const canCmp = list.data.length > 1
  const body = usePromptBody(slug, sel.version)
  const base = usePromptBody(slug, mode === 'compare' ? from : null)
  const text = body.data?.data.body
  const baseText = base.data?.data.body

  const copy = () => {
    if (text === undefined) return
    void navigator.clipboard?.writeText(text).then(
      () => toast({ tone: 'success', title: t('pr.copied') }),
      () => undefined,
    )
  }

  const useBtn =
    sel.version === u.inUse ? null : sel.version === u.file ? (
      <Button variant="primary" size="sm" loading={unpinning} onClick={onUnpin} data-testid="use-file">
        {t('pr.useFile')}
      </Button>
    ) : (
      <Button variant="primary" size="sm" onClick={onUse} data-testid="use-version">
        {t('pr.use', { n: sel.version })}
      </Button>
    )

  const failed = body.isError || (mode === 'compare' && base.isError)
  return (
    <section className="pmt-view" aria-labelledby="pmt-view-title">
      <div className="pmt-view-head">
        <div className="pmt-view-title">
          <h3 className="text-headline" id="pmt-view-title">
            {t('pr.v', { n: sel.version })}
          </h3>
          <span className="pmt-view-meta">
            <span className="tabular-nums">{t('pr.saved', { date: formatDayTime(sel.created_at, lang) })}</span>
            <code dir="ltr">{sel.hash12}</code>
            {text !== undefined && <span>{t('pr.lines', { count: linesOf(text).length, n: formatFixed(linesOf(text).length, 0, lang) })}</span>}
          </span>
        </div>
        <div className="pmt-view-tools">
          <SegmentedControl<'read' | 'compare'>
            aria-label={t('pr.mode')}
            options={[
              { value: 'read', label: t('pr.read') },
              { value: 'compare', label: t('pr.compare'), disabled: !canCmp, tip: canCmp ? undefined : t('pr.only') },
            ]}
            value={mode}
            onValueChange={onMode}
          />
          {mode === 'compare' && (
            <label className="pmt-from">
              <span>{t('pr.from')}</span>
              <span className="pmt-select">
                <select value={from ?? ''} onChange={(e) => onFrom(Number(e.target.value))} data-testid="compare-from">
                  {list.data
                    .filter((v) => v.version !== sel.version)
                    .map((v) => (
                      <option key={v.version} value={v.version}>
                        {t('pr.v', { n: v.version })}
                      </option>
                    ))}
                </select>
                <ChevronsUpDown aria-hidden="true" strokeWidth={2} />
              </span>
            </label>
          )}
          <Button variant="plain" size="sm" onClick={copy} disabled={text === undefined} aria-label={t('pr.copy')} title={t('pr.copy')}>
            <Copy aria-hidden="true" strokeWidth={1.75} />
          </Button>
          {useBtn}
        </div>
      </div>
      {!canCmp && (
        <p className="pmt-only">
          <Info aria-hidden="true" strokeWidth={1.75} />
          <span>{t('pr.only')}</span>
        </p>
      )}
      {failed ? (
        <p className="pmt-text-err" role="alert">
          <CircleAlert aria-hidden="true" strokeWidth={1.75} className="size-4" />
          <span>{t('pr.textError')}</span>
          <Button variant="secondary" size="sm" onClick={() => void Promise.all([body.refetch(), base.refetch()])}>
            {t('pr.err.retry')}
          </Button>
        </p>
      ) : text === undefined || (mode === 'compare' && baseText === undefined) ? (
        <TextSkeleton />
      ) : mode === 'compare' && baseText !== undefined ? (
        <>
          <DiffCounts from={baseText} to={text} />
          <PromptDiff key={`${from}-${sel.version}`} from={baseText} to={text} />
        </>
      ) : (
        <PromptReader body={text} />
      )}
    </section>
  )
}

export function PromptHistoryPage() {
  const { t } = useTranslation('settings-agents')
  const { t: tw } = useTranslation('warroom')
  const [params, setParams] = useSearchParams()
  const rawAgent = params.get('agent')
  const slug: AgentSlug = isSlug(rawAgent) ? rawAgent : 'historian'
  const name = tw(`agents.${slug}.name`)
  const list = usePromptVersions(slug)
  const unpin = usePinPrompt(slug)
  const [sheetFor, setSheetFor] = useState<PromptVersion | null>(null)

  const go = (next: { v?: number | null; mode?: 'compare' | null; from?: number | null; agent?: AgentSlug }) => {
    const p = new URLSearchParams()
    p.set('agent', next.agent ?? slug)
    if (next.v != null) p.set('v', String(next.v))
    if (next.mode) p.set('mode', next.mode)
    if (next.from != null) p.set('from', String(next.from))
    setParams(p, { replace: next.agent === undefined })
  }

  const head = <Head slug={slug} onAgent={(agent) => go({ agent })} />

  if (list.isPending)
    return (
      <div className="st-page pmt-page" aria-busy="true" data-testid="prompts-loading">
        {head}
        <div className="pmt-split">
          <div className="pmt-rail-sk">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-16 rounded-card" />
            ))}
          </div>
          <div className="pmt-view">
            <Skeleton className="h-[18px] w-[40%]" />
            <TextSkeleton />
          </div>
        </div>
      </div>
    )

  if (list.isError || !list.data)
    return (
      <div className="st-page pmt-page">
        {head}
        <section className="st-group st-empty" role="alert" data-testid="prompts-error">
          <span className="st-empty-icon is-error" aria-hidden="true">
            <CircleAlert strokeWidth={1.5} />
          </span>
          <h2 className="text-title-3">{t('pr.err.title')}</h2>
          <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('pr.err.body')}</p>
          <Button variant="secondary" className="mt-2" icon={<RefreshCw />} onClick={() => void list.refetch()}>
            {t('pr.err.retry')}
          </Button>
        </section>
      </div>
    )

  const data = list.data
  if (data.data.length === 0)
    return (
      <div className="st-page pmt-page">
        {head}
        <section className="st-group st-empty" role="status" data-testid="prompts-empty">
          <span className="st-empty-icon" aria-hidden="true">
            <FileClock strokeWidth={1.5} />
          </span>
          <h2 className="text-title-3">{t('pr.empty.title')}</h2>
          <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('pr.empty.body', { name })}</p>
        </section>
      </div>
    )

  const u = usage(data)
  const has = (v: number | null) => v !== null && data.data.some((x) => x.version === v)
  const fallback = u.inUse !== null && has(u.inUse) ? u.inUse : (data.data[0]?.version ?? 1)
  const selN = has(num(params.get('v'))) ? (num(params.get('v')) as number) : fallback
  const sel = data.data.find((x) => x.version === selN) ?? (data.data[0] as PromptVersion)
  const canCmp = data.data.length > 1
  const mode = canCmp && params.get('mode') === 'compare' ? 'compare' : 'read'
  const fromQ = num(params.get('from'))
  const older = data.data.find((x) => x.version < sel.version)
  const from = has(fromQ) && fromQ !== sel.version ? fromQ : (older ?? data.data.find((x) => x.version !== sel.version))?.version ?? null

  const doUnpin = () =>
    unpin.mutate(
      { version: null },
      {
        onSuccess: () => {
          go({ v: u.file })
          toast({ tone: 'success', title: t('pr.toastFile', { name }), body: t('pr.toastFileBody', { n: u.file ?? '—' }) })
        },
        onError: (err) => toast({ tone: 'error', title: pinErrorText(err, t) }),
      },
    )

  return (
    <div className="st-page pmt-page">
      {head}
      <Status slug={slug} name={name} list={data} onUnpin={doUnpin} unpinning={unpin.isPending} />
      <div className="pmt-split">
        <Rail list={data} sel={sel.version} onSelect={(v) => go({ v, mode: mode === 'compare' ? 'compare' : null })} />
        <View
          slug={slug}
          list={data}
          sel={sel}
          mode={mode}
          from={from}
          onMode={(m) => go({ v: sel.version, mode: m === 'compare' ? 'compare' : null, from: m === 'compare' ? from : null })}
          onFrom={(f) => go({ v: sel.version, mode: 'compare', from: f })}
          onUse={() => setSheetFor(sel)}
          onUnpin={doUnpin}
          unpinning={unpin.isPending}
        />
      </div>
      {sheetFor && (
        <UsePromptSheet
          slug={slug}
          name={name}
          version={sheetFor}
          fileVersion={u.file}
          open
          onOpenChange={(o) => !o && setSheetFor(null)}
          onPinned={(v) => go({ v })}
        />
      )}
    </div>
  )
}
