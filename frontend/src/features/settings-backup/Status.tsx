import { CircleAlert, CircleCheck, CircleX, DatabaseBackup, TriangleAlert, X } from 'lucide-react'
import { useRef, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button, Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice } from '../onboarding'
import { formatTime } from '../warroom/time'
import type { BackupDoc } from './api'
import { backupStatus, bytesText, keptBytes, mmss, ms, nextRun, okRuns, shelfSlots, teamInstant, type PendingRun } from './model'
import { dayName, Doctor, fill, isolate, Ltr, NS, useWhen } from './parts'

const DAY = 86_400_000

export function NowButton({ busy, primary, onRun }: { busy: boolean; primary?: boolean; onRun: () => void }) {
  const { t } = useTranslation(NS)
  return (
    <Button
      variant={primary ? 'primary' : 'secondary'}
      className="bk-now-btn"
      loading={busy}
      disabled={busy}
      aria-busy={busy || undefined}
      icon={busy ? undefined : <DatabaseBackup />}
      onClick={onRun}
      data-testid="bk-now"
    >
      {t(busy ? 'nowBusy' : 'now')}
    </Button>
  )
}

/** The shelf: 14 Baghdad days, today at the inline end. One tab stop; arrow keys move between days. */
function Shelf({ doc, now, lang, running }: { doc: BackupDoc; now: number; lang: Lang; running: boolean }) {
  const { t } = useTranslation(NS)
  const ref = useRef<HTMLOListElement>(null)
  const slots = shelfSlots(doc, now)
  const onKey = (e: KeyboardEvent<HTMLSpanElement>, i: number) => {
    const rtl = document.documentElement.dir === 'rtl'
    let n: number | null = null
    if (e.key === 'ArrowRight') n = rtl ? i - 1 : i + 1
    if (e.key === 'ArrowLeft') n = rtl ? i + 1 : i - 1
    if (e.key === 'Home') n = 0
    if (e.key === 'End') n = slots.length - 1
    if (n === null) return
    e.preventDefault()
    n = Math.max(0, Math.min(slots.length - 1, n))
    const bays = [...(ref.current?.querySelectorAll<HTMLSpanElement>('.bk-bay') ?? [])]
    bays.forEach((b, j) => (b.tabIndex = j === n ? 0 : -1))
    bays[n]?.focus()
  }
  return (
    <ol ref={ref} className="bk-shelf" aria-label={t('shelf')} data-testid="bk-shelf">
      {slots.map((s, i) => {
        const day = dayName(Date.parse(`${s.iso}T09:00:00Z`), lang)
        const at = s.run ? isolate(formatTime(ms(s.run.started_at), lang)) : isolate(formatTime(teamInstant(s.iso, doc.settings.time, now), lang))
        let label =
          s.kind === 'ok' && s.run
            ? t('slot.ok', { day, time: at, size: isolate(bytesText(s.run.bytes ?? 0, lang)) })
            : s.kind === 'failed' && s.run
              ? t('slot.failed', { day, time: at, why: t(/No such file/.test(s.run.error ?? '') ? 'why.folder' : 'why.other') })
              : s.kind === 'unknown'
                ? t('slot.unknown', { day })
                : t('slot.missed', { day, time: at })
        const live = s.today && running
        if (live) label = t('slot.running') + (s.kind === 'ok' ? `. ${label}` : '')
        const dd = Number(s.iso.slice(8, 10))
        return (
          <li key={s.iso} className={cx('bk-slot', s.today && 'is-today')}>
            <span
              className={cx('bk-bay', `is-${s.kind}`, s.latest && 'is-latest', live && 'is-running')}
              tabIndex={i === slots.length - 1 ? 0 : -1}
              title={label}
              aria-label={label}
              role="img"
              data-kind={s.kind}
              onKeyDown={(e) => onKey(e, i)}
            >
              {s.kind === 'ok' && <span className="bk-fill" style={{ ['--v' as string]: s.v.toFixed(3) }} />}
              {s.kind === 'failed' && <X className="bk-x" aria-hidden="true" />}
            </span>
            <span className="bk-day" aria-hidden="true">
              {s.today ? t('today') : <Ltr className="tabular-nums">{dd}</Ltr>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export function StatusCard({
  doc,
  now,
  lang,
  pending,
  onRun,
}: {
  doc: BackupDoc
  now: number
  lang: Lang
  pending: PendingRun | null
  onRun: () => void
}) {
  const { t } = useTranslation(NS)
  const when = useWhen()
  const st = backupStatus(doc, pending)
  const good = doc.last_good
  const busy = st === 'running'

  if (st === 'none') {
    return (
      <section className="st-group bk-hero is-none" aria-labelledby="bk-none-title" data-testid="bk-status" data-status="none">
        <div className="st-empty bk-empty">
          <span className="st-empty-icon is-warn" aria-hidden="true">
            <TriangleAlert strokeWidth={1.5} />
          </span>
          <h2 className="text-title-3" id="bk-none-title">
            {t('none.title')}
          </h2>
          <p className="max-w-[46ch] text-subheadline text-label-secondary">
            {doc.settings.enabled ? t('none.body', { when: when(nextRun(doc.settings.time, now), now, lang, true) }) : t('none.bodyOff')}
          </p>
          <NowButton busy={false} primary onRun={onRun} />
        </div>
      </section>
    )
  }

  const icon = busy ? (
    <Spinner />
  ) : st === 'ok' ? (
    <CircleCheck className="bk-st-icon is-ok" aria-hidden="true" />
  ) : st === 'failed' ? (
    <CircleX className="bk-st-icon is-error" aria-hidden="true" />
  ) : (
    <TriangleAlert className="bk-st-icon is-warn" aria-hidden="true" />
  )
  const ok = okRuns(doc.runs)
  const oldest = ok.slice(0, doc.settings.keep).at(-1)
  const kept = keptBytes(doc)
  const lastRun = doc.runs[0]
  const ageMs = good ? good.age_seconds * 1000 : 0

  return (
    <section className={cx('st-group bk-hero', `is-${st}`)} aria-label={t('latest')} data-testid="bk-status" data-status={st}>
      <div className="bk-top">
        <div className="bk-now-col">
          {busy && pending ? (
            <>
              <p className="bk-kicker">
                {icon}
                <span>{t('runningLabel')}</span>
              </p>
              <p className="bk-when">
                <Ltr className="bk-elapsed tabular-nums">{mmss(now - pending.requestedAt)}</Ltr>
              </p>
              <p className="bk-file">{t('elapsed', { time: isolate(formatTime(pending.requestedAt, lang)) })}</p>
            </>
          ) : (
            <>
              <p className="bk-kicker">
                {icon}
                <span>{t('latest')}</span>
              </p>
              <p className={cx('bk-when', st === 'failed' && 'is-error', st === 'overdue' && 'is-warn')} data-testid="bk-when">
                {good ? when(ms(good.started_at), now, lang) : '—'}
              </p>
              {good && (
                <p className="bk-file">
                  <Ltr className="bk-size tabular-nums" testId="bk-size">
                    {bytesText(good.bytes ?? 0, lang)}
                  </Ltr>
                  <Ltr className="bk-name" testId="bk-file">
                    {good.file}
                  </Ltr>
                </p>
              )}
            </>
          )}
          <div className="bk-actions">
            <NowButton busy={busy} onRun={onRun} />
          </div>
        </div>
        <div className="bk-shelf-col">
          <p className="bk-shelf-title" aria-hidden="true">
            {t('shelf')}
          </p>
          <Shelf doc={doc} now={now} lang={lang} running={busy} />
        </div>
      </div>

      {st === 'overdue' && (
        <Notice tone="warning" icon={TriangleAlert} role="status" className="bk-notice" title={t('overdue.title', { count: Math.max(1, Math.floor(ageMs / DAY)), days: t('days', { count: Math.max(1, Math.floor(ageMs / DAY)) }) })} data-testid="bk-overdue">
          {fill(t('overdue.body'), { cmd: <Doctor /> })}
        </Notice>
      )}
      {st === 'failed' && lastRun && (
        <Notice tone="error" icon={CircleAlert} role="alert" className="bk-notice" title={t('failed.title')} data-testid="bk-failed">
          <span className="bk-err" dir="ltr">
            {lastRun.error}
          </span>
          <span className="bk-err-body">
            {t('failed.body')}
            {doc.stale && <> {fill(t('failed.doctor'), { cmd: <Doctor /> })}</>}
          </span>
        </Notice>
      )}

      <dl className="bk-facts">
        <div>
          <dt>{t('next')}</dt>
          <dd data-testid="bk-next">{doc.settings.enabled ? when(nextRun(doc.settings.time, now), now, lang) : t('nextOff')}</dd>
        </div>
        <div>
          <dt>{t('oldest')}</dt>
          <dd>{oldest ? dayName(ms(oldest.started_at), lang) : '—'}</dd>
        </div>
        <div>
          <dt>{t('space')}</dt>
          <dd>
            {fill(t('spaceVal'), {
              size: <Ltr className="bk-size tabular-nums">{bytesText(kept.bytes, lang)}</Ltr>,
              copies: t('copies', { count: kept.copies }),
            })}
          </dd>
        </div>
      </dl>
    </section>
  )
}
