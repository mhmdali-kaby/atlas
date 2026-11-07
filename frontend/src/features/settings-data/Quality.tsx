import { CircleCheck, CircleSlash, Clock, Copy, Info, Sigma, SquareDashed } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, SegmentedControl } from '../../ui'
import type { ApiIssue, IssueKind } from './api'
import { issueBars, sortIssues, tKey } from './model'
import { Fig, InstId, NS, Tip, Tr, useTimeText } from './parts'

/* Data quality (design §5.4): open issues and gaps repaired this week. */

const KIND_ICON: Record<IssueKind, typeof SquareDashed> = {
  gap: SquareDashed,
  duplicate: Copy,
  outlier: Sigma,
  // TL (v5 review): signal-zero rendered as a near-invisible dot; circle-slash reads at 16px.
  zero_volume: CircleSlash,
}

function IssueTitle({ q }: { q: ApiIssue }) {
  const { t } = useTranslation(NS)
  const bar = t(`bar.${tKey(q.dataset)}`, { defaultValue: q.dataset })
  if (q.kind === 'outlier') {
    const sigma = typeof q.detail?.sigma === 'number' ? q.detail.sigma : null
    return sigma === null ? <>{t('q.outlierPlain', { bar })}</> : <Tr k="q.outlier" values={{ sigma: `${sigma.toFixed(1)}σ`, bar }} />
  }
  const n = issueBars(q)
  return <Tr k={`q.${q.kind}`} count={n} values={{ n, bar }} />
}

function IssueStatus({ q, nowMs }: { q: ApiIssue; nowMs: number }) {
  const { t } = useTranslation(NS)
  const { shortWhen } = useTimeText()
  if (q.repaired_at)
    return (
      <span className="dt-issue-status is-repaired">
        <CircleCheck aria-hidden="true" strokeWidth={1.75} />
        <span>
          {t('q.repairedAt', { when: '\u0000' })
            .split('\u0000')
            .map((part, k) => (
              <span key={k}>
                {k > 0 && shortWhen(q.repaired_at!, nowMs)}
                {part}
              </span>
            ))}
        </span>
      </span>
    )
  if (q.kind === 'gap')
    return (
      <Tip label={t('q.notRepairedTip')}>
        <span className="dt-issue-status is-open" tabIndex={0}>
          <Clock aria-hidden="true" strokeWidth={1.75} />
          <span>{t('q.notRepaired')}</span>
        </span>
      </Tip>
    )
  const tip = q.kind === 'duplicate' ? 'q.duplicateTip' : q.kind === 'outlier' ? 'q.outlierTip' : 'q.zeroTip'
  const label = q.kind === 'duplicate' ? 'q.logged' : issueBars(q) > 1 ? 'q.keptMany' : 'q.keptOne'
  return (
    <Tip label={t(tip)}>
      <span className="dt-issue-status" tabIndex={0}>
        <Info aria-hidden="true" strokeWidth={1.75} />
        <span>{t(label)}</span>
      </span>
    </Tip>
  )
}

export function IssueRow({ q, nowMs, noInst }: { q: ApiIssue; nowMs: number; noInst?: boolean }) {
  const { t } = useTranslation(NS)
  const { windowText, shortWhen } = useTimeText()
  const Icon = KIND_ICON[q.kind]
  const state = q.repaired_at ? 'is-repaired' : q.kind === 'gap' ? 'is-open' : 'is-logged'
  return (
    <div className="dt-row dt-issue" data-testid="dt-issue" data-kind={q.kind}>
      <span className={`dt-row-icon ${state}`} aria-hidden="true">
        <Icon strokeWidth={1.75} />
      </span>
      <span className="dt-row-main">
        <span className="dt-row-label">
          <IssueTitle q={q} />
        </span>
        <span className="dt-row-sub">
          {!noInst && <InstId id={q.inst_id} />}
          <span>{windowText(q.from_ts, q.to_ts)}</span>
          <span>
            {t('q.found', { when: '\u0000' })
              .split('\u0000')
              .map((part, k) => (
                <span key={k}>
                  {k > 0 && shortWhen(q.detected_at, nowMs)}
                  {part}
                </span>
              ))}
          </span>
        </span>
      </span>
      <IssueStatus q={q} nowMs={nowMs} />
    </div>
  )
}

const VISIBLE = 6

export function Quality({ issues, nowMs }: { issues: ApiIssue[]; nowMs: number }) {
  const { t } = useTranslation(NS)
  const [tab, setTab] = useState<'open' | 'repaired'>('open')
  const [all, setAll] = useState(false)
  const open = sortIssues(issues, 'open')
  const repaired = sortIssues(issues, 'repaired')
  const list = tab === 'open' ? open : repaired
  const shown = all ? list : list.slice(0, VISIBLE)
  return (
    <section className="dt-section" aria-labelledby="dt-q-title" data-testid="dt-quality">
      <div className="dt-sec-head">
        <h2 className="dt-group-title" id="dt-q-title">
          {t('q.title')}
        </h2>
        <SegmentedControl
          aria-label={t('q.title')}
          value={tab}
          onValueChange={(v) => {
            setTab(v)
            setAll(false)
          }}
          options={[
            { value: 'open', label: <>{t('q.open')} <Fig>{open.length}</Fig></> },
            { value: 'repaired', label: <>{t('q.repaired')} <Fig>{repaired.length}</Fig></> },
          ]}
        />
      </div>
      <div className="dt-group">
        {list.length === 0 ? (
          <div className="dt-quiet">
            <span className="dt-row-icon" aria-hidden="true">
              <CircleCheck strokeWidth={1.75} />
            </span>
            <span className="text-subheadline text-label-secondary">{t(tab === 'open' ? 'q.noneOpen' : 'q.noneRepaired')}</span>
          </div>
        ) : (
          <>
            {shown.map((q) => (
              <IssueRow key={q.id} q={q} nowMs={nowMs} />
            ))}
            {list.length > VISIBLE && (
              <div className="dt-more">
                <Button size="sm" variant="plain" aria-expanded={all} onClick={() => setAll((a) => !a)}>
                  {all ? t('q.fewer') : <Tr k="q.more" values={{ n: list.length }} />}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
      <p className="dt-group-note">{t('q.note')}</p>
    </section>
  )
}
