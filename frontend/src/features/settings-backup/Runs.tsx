import { Check, ChevronRight, CircleCheck, CircleX, Copy, Info, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button, Spinner, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice } from '../onboarding'
import { formatTime } from '../warroom/time'
import type { BackupDoc, BackupRun } from './api'
import { bytesText, LIST_SHORT, mmss, ms, restoreCommands, type PendingRun } from './model'
import { copyText, dayName, fill, isolate, Ltr, NS } from './parts'

function RunRow({ run, lang, chosen, onChoose }: { run: BackupRun; lang: Lang; chosen: boolean; onChoose: () => void }) {
  const { t } = useTranslation(NS)
  const at = ms(run.started_at)
  const day = dayName(at, lang)
  return (
    <li className={cx('bk-run', `is-${run.status}`, chosen && 'is-chosen')} data-testid="bk-run" data-status={run.status}>
      <span className="bk-run-icon" aria-hidden="true">
        {run.status === 'ok' ? <CircleCheck className="is-ok" /> : <CircleX className="is-error" />}
      </span>
      <span className="bk-run-main">
        <span className="bk-run-when">
          {day}
          <Ltr className="bk-run-time tabular-nums">{formatTime(at, lang)}</Ltr>
          <span className="sr-only">, {t(`st.${run.status}`)}</span>
        </span>
        {run.status === 'failed' ? (
          <span className="bk-run-err" dir="ltr" data-testid="bk-run-error">
            {run.error}
          </span>
        ) : (
          <span className="bk-run-sub">
            <Ltr className="bk-name">{run.file}</Ltr>
            {run.finished_at && <span>{t('took', { d: isolate(mmss(ms(run.finished_at) - at)) })}</span>}
          </span>
        )}
      </span>
      <span className="bk-run-size">
        {run.status === 'ok' ? (
          <Ltr className="tabular-nums">{bytesText(run.bytes ?? 0, lang)}</Ltr>
        ) : (
          <span className="bk-run-st is-failed">{t('st.failed')}</span>
        )}
      </span>
      <span className="bk-run-acts">
        {run.status === 'ok' && run.path && (
          <>
            <Button
              variant="plain"
              size="sm"
              className="bk-copy"
              aria-label={t('copyPathAria', { day })}
              title={t('copyPath')}
              icon={<Copy />}
              onClick={() => {
                copyText(run.path ?? '')
                toast({ tone: 'success', title: t('pathCopied'), body: run.path ?? '' })
              }}
            />
            <Button variant="plain" size="sm" className={cx('bk-use', chosen && 'is-chosen')} aria-pressed={chosen} icon={chosen ? <Check /> : undefined} onClick={onChoose}>
              {t(chosen ? 'used' : 'use')}
            </Button>
          </>
        )}
      </span>
    </li>
  )
}

export function RunsList({
  doc,
  lang,
  pending,
  now,
  chosen,
  onChoose,
}: {
  doc: BackupDoc
  lang: Lang
  pending: PendingRun | null
  now: number
  chosen: string | null
  onChoose: (id: string) => void
}) {
  const { t } = useTranslation(NS)
  const [all, setAll] = useState(false)
  const rows = doc.runs
  if (!rows.length && !pending) return null
  const shown = all ? rows : rows.slice(0, LIST_SHORT)
  return (
    <section className="grid" aria-labelledby="bk-list-title">
      <h2 className="bk-group-title" id="bk-list-title">
        {t('list')}
      </h2>
      <div className="st-group bk-list">
        <ul className="bk-runs" data-testid="bk-runs">
          {pending && (
            <li className="bk-run is-running" data-testid="bk-run" data-status="running">
              <span className="bk-run-icon" aria-hidden="true">
                <Spinner />
              </span>
              <span className="bk-run-main">
                <span className="bk-run-when">
                  {dayName(pending.requestedAt, lang)}
                  <Ltr className="bk-run-time tabular-nums">{formatTime(pending.requestedAt, lang)}</Ltr>
                </span>
                <span className="bk-run-sub">
                  <Ltr className="tabular-nums">{mmss(now - pending.requestedAt)}</Ltr>
                </span>
              </span>
              <span className="bk-run-size">
                <span className="bk-run-st is-running">{t('st.running')}</span>
              </span>
              <span className="bk-run-acts" />
            </li>
          )}
          {shown.map((r) => (
            <RunRow key={r.id} run={r} lang={lang} chosen={r.id === chosen} onChoose={() => onChoose(r.id)} />
          ))}
        </ul>
        {rows.length > LIST_SHORT && (
          <div className="bk-more">
            <button type="button" className="bk-more-btn" aria-expanded={all} onClick={() => setAll(!all)}>
              <ChevronRight className={cx('rtl:-scale-x-100', all && 'is-open')} aria-hidden="true" />
              <span>{all ? t('showFewer') : t('showAll', { n: isolate(String(rows.length)) })}</span>
            </button>
          </div>
        )}
      </div>
      <p className="bk-group-note">{t('listNote', { count: doc.settings.keep })}</p>
    </section>
  )
}

function CmdCopy({ text, n }: { text: string; n: number }) {
  const { t } = useTranslation(NS)
  const [done, setDone] = useState(false)
  return (
    <Button
      variant="plain"
      size="sm"
      className={cx('bk-cmd-copy', done && 'is-done')}
      aria-label={t('r.copyAria', { n })}
      icon={done ? <Check /> : <Copy />}
      onClick={() => {
        copyText(text)
        setDone(true)
        window.setTimeout(() => setDone(false), 1600)
      }}
    >
      {t(done ? 'r.copied' : 'r.copy')}
    </Button>
  )
}

/** How to restore: docs/RUNBOOK.md's steps in plain words, with the chosen dump filled in. Atlas runs none of them. */
export function Restore({ doc, chosen }: { doc: BackupDoc; chosen: string | null }) {
  const { t } = useTranslation(NS)
  const run = doc.runs.find((r) => r.id === chosen && r.status === 'ok' && r.path)
  return (
    <section className="grid" aria-labelledby="bk-r-title" data-testid="bk-restore">
      <h2 className="bk-group-title" id="bk-r-title">
        {t('restore')}
      </h2>
      {!run?.path ? (
        <div className="st-group bk-restore is-empty">
          <p className="bk-r-none">
            <Info aria-hidden="true" />
            <span>{t('r.none')}</span>
          </p>
        </div>
      ) : (
        <div className="st-group bk-restore">
          <Notice tone="warning" icon={TriangleAlert} title={t('r.warn')} />
          <ol className="bk-steps">
            {restoreCommands(run.path).map((cmd, i) => (
              <li key={i} className="bk-step">
                <span className="bk-step-n" aria-hidden="true">
                  <Ltr className="tabular-nums">{i + 1}</Ltr>
                </span>
                <div className="bk-step-body">
                  <p className="bk-step-text">{t(`r.s${i + 1}`)}</p>
                  <div className={cx('bk-cmd', i === 1 && 'is-long')}>
                    <code dir="ltr" data-testid={`bk-cmd-${i + 1}`}>
                      {cmd}
                    </code>
                    <CmdCopy text={cmd} n={i + 1} />
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="bk-group-note">{fill(t('r.note'), { file: <code dir="ltr">docs/RUNBOOK.md</code> })}</p>
    </section>
  )
}
