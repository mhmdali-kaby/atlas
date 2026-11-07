import { RefreshCw } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Sheet, SheetClose } from '../../ui'
import { DATASETS, exact, jobStatus, pctText, type DatasetKey, type Inst, type Model, tKey } from './model'
import { InstId, NS, StatusPill, Tr, useDepthText } from './parts'
import { IssueRow } from './Quality'
import { Lane } from './Summary'

/* The instrument sheet (design §5.7): one history lane per dataset with gap ticks, errors verbatim,
   the instrument's issues, and Re-sync this instrument. */

export function InstrumentSheet({
  inst,
  model,
  nowMs,
  busy,
  onClose,
  onResync,
  depthOf,
}: {
  inst: Inst | null
  model: Model
  nowMs: number
  busy: boolean
  onClose: () => void
  onResync: (id: string, failed: boolean) => void
  depthOf: (k: DatasetKey) => number | null
}) {
  const { t } = useTranslation(NS)
  const depthText = useDepthText()
  const closeRef = useRef<HTMLButtonElement>(null)
  const open = inst !== null

  useEffect(() => {
    if (!open) return
    const id = window.setTimeout(() => closeRef.current?.focus({ preventScroll: true }), 30)
    return () => window.clearTimeout(id)
  }, [open])

  const issues = inst
    ? model.issues
        .filter((q) => q.inst_id === inst.instId)
        .sort((a, b) => Date.parse(b.detected_at) - Date.parse(a.detected_at))
    : []
  const hasTicks = issues.some((q) => q.kind === 'gap')

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose()
      }}
      className="dt-sheet"
      title={inst ? <InstId id={inst.instId} /> : ''}
      description={
        inst ? (
          inst.status === 'done' ? (
            t('sh.subDone', { cls: t(`class1.${inst.assetClass}`) })
          ) : (
            <Tr k="sh.sub" values={{ cls: t(`class1.${inst.assetClass}`), pct: pctText(inst.pct) }} />
          )
        ) : undefined
      }
      actions={
        inst ? (
          <>
            <SheetClose>
              <Button ref={closeRef} variant="secondary">
                {t('close')}
              </Button>
            </SheetClose>
            <Button variant="primary" icon={<RefreshCw />} loading={busy} onClick={() => onResync(inst.instId, inst.status === 'failed')}>
              {t(busy ? 'resyncing' : 'sh.resync')}
            </Button>
          </>
        ) : undefined
      }
    >
      {inst && (
        <div className="dt-sh-body" data-testid="dt-sheet">
          <StatusPill status={inst.status} />
          <ul className="dt-lanes">
            {DATASETS.map((d) => {
              const job = inst.jobs[d.key]
              if (!job) return null
              const st = jobStatus(job)
              const start = Date.parse(job.target_from ?? job.cursor_ts ?? '')
              const span = nowMs - start
              const gaps = Number.isNaN(start) || span <= 0
                ? []
                : issues
                    .filter((q) => q.kind === 'gap' && q.dataset === d.key)
                    .map((q) => ({ at: Math.max(0, Math.min(1, (Date.parse(q.from_ts) - start) / span)), repaired: q.repaired_at !== null }))
              const depth = depthText(d.key, depthOf(d.key))
              return (
                <li className="dt-lane-row" key={d.key} data-ds={d.key}>
                  <span className="dt-lane-name">
                    {t(`ds.${tKey(d.key)}`)}
                    <small>{depth}</small>
                  </span>
                  <Lane job={job} dataset={d.key} stacked nowMs={nowMs} gaps={gaps} />
                  <span className="dt-lane-meta">
                    <span>
                      {d.open || job.expected === null ? (
                        <Tr k="records" count={job.rows_written} values={{ n: exact(job.rows_written) }} />
                      ) : (
                        <Tr k="ofBars" values={{ w: exact(Math.min(job.rows_written, job.expected)), e: exact(job.expected) }} />
                      )}
                    </span>
                    <StatusPill status={st} />
                  </span>
                  {st === 'failed' && job.last_error && (
                    <div className="dt-lane-error">
                      <p>{t('sh.error')}</p>
                      <code dir="ltr">{job.last_error}</code>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
          {hasTicks && (
            <p className="dt-sh-key">
              <span>
                <i />
                {t('sh.keyGap')}
              </span>
              <span>
                <i className="is-repaired" />
                {t('sh.keyRepaired')}
              </span>
            </p>
          )}
          <section className="dt-sh-issues">
            <h3>{t('sh.issues')}</h3>
            {issues.length > 0 ? (
              <div className="dt-group">
                {issues.map((q) => (
                  <IssueRow key={q.id} q={q} nowMs={nowMs} noInst />
                ))}
              </div>
            ) : (
              <p className="dt-sh-none">{t('sh.noIssues')}</p>
            )}
          </section>
        </div>
      )}
    </Sheet>
  )
}
