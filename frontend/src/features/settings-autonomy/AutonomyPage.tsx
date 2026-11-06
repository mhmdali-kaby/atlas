import { Bell, ChevronRight, OctagonX, Shield, ShieldCheck } from 'lucide-react'
import { useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed } from '../../lib/format'
import { Button } from '../../ui'
import { cx } from '../../ui/cx'
import {
  askInputText,
  AskField,
  AskLine,
  AskSentence,
  AutonomyPicker,
  enabledAutonomyLevels,
  MoneyFig,
  parseAsk,
  TurnOnSheet,
  useSettings,
} from '../onboarding'
import { useLang } from '../portfolio/hooks'
import '../settings-connections/settings.css'
import { useRiskPreview } from '../settings-money/model'
import { fill } from '../settings-money/parts'
import { EnvScopeNotice, useEnvScope, type EnvScope } from '../settings-risk/EnvScope'
import { useSettingsHistory } from '../settings-risk/model'
import '../settings-risk/risk.css'
import './autonomy.css'
import { dayText } from '../settings-environment/model'
import { savedAsk } from './model'
import { Changes, LevelName, LoadError, LoadingBody, PaneHeader, Section } from './parts'
import { useAutonomySave } from './useAutonomySave'

/*
 * Settings → Autonomy (brief §14.6, decision 254, ATL-216; design js/pages/settings-autonomy.js).
 * Leads with what is in force now: the sentence and the ask line. Raising autonomy (to 3 or 4,
 * or a higher amount at 3) opens the turn-on sheet, which saves; lowering saves at once.
 * There is no save bar on this pane.
 */

/** config/defaults.yaml autonomy.approval_timeout_minutes; a settings value overrides it. */
const DEFAULT_APPROVAL_TIMEOUT_MINUTES = 15

const ALWAYS = [
  [ShieldCheck, 'a1'],
  [Shield, 'a2'],
  [OctagonX, 'a3'],
] as const

export function AutonomyPage() {
  const scope = useEnvScope()
  // One row per env: switching Demo | Live starts from that row's saved values, no edit carried over.
  return <AutonomyPane key={scope.env} scope={scope} />
}

function AutonomyPane({ scope }: { scope: EnvScope }) {
  const rowEnv = scope.env
  const { t } = useTranslation('settings-autonomy')
  const { t: tAu } = useTranslation('autonomy')
  const lang = useLang()
  const query = useSettings(rowEnv)
  const history = useSettingsHistory(rowEnv)
  const preview = useRiskPreview(`env=${rowEnv}`, rowEnv)
  const levelsRef = useRef<HTMLDivElement>(null)
  const s = useAutonomySave(query, levelsRef, rowEnv)
  const doc = query.data?.data

  const largestRaw = preview.data?.data.max_notional_usd ?? null
  const largest = largestRaw === null ? null : Number(largestRaw)

  let body: ReactNode
  if (query.isPending) {
    body = <LoadingBody />
  } else if (!doc) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else {
    const level = doc.settings.autonomy_level
    const ask = Number(savedAsk(doc))
    const choice = s.sheet?.level ?? s.busyLevel ?? level
    const editText = s.askEdit ?? askInputText(savedAsk(doc))
    const dirty = s.askEdit !== null && parseAsk(s.askEdit) !== ask
    // First live month (ATL-238/ATL-240): 3 and 4 are refused on the live row until the end date.
    const monthEnds = doc.first_live_month_ends_at ?? null
    const endsText = monthEnds ? dayText(monthEnds, lang, true) : null
    const timeout = Number(doc.settings.approval_timeout_minutes ?? DEFAULT_APPROVAL_TIMEOUT_MINUTES)
    const field =
      level === 3 && choice === 3 ? (
        <AskField
          id="au-ask"
          text={editText}
          error={s.askError}
          disabled={s.pending}
          onText={s.editAsk}
          onEnter={dirty ? s.saveAmount : undefined}
          actions={
            dirty ? (
              <>
                <Button variant="plain" size="sm" disabled={s.pending} onClick={s.revertAsk}>
                  {tAu('revertAmount')}
                </Button>
                <Button variant="primary" size="sm" loading={s.pending && !s.sheet} onClick={s.saveAmount} data-testid="au-save-amount">
                  {tAu('saveAmount')}
                </Button>
              </>
            ) : undefined
          }
        />
      ) : undefined

    body = (
      <>
        <Section id="au-now-title" title={t('now')}>
          <div key={s.flash} className={cx('st-group au-now', s.flash > 0 && 'is-flash')} data-testid="autonomy-now" data-level={level}>
            <p className="au-now-head">
              <span className="au-now-tile tabular-nums" aria-hidden="true">
                {formatFixed(level, 0, lang)}
              </span>
              <span>{t('levelN', { n: formatFixed(level, 0, lang) })}</span>
              <LevelName level={level} />
            </p>
            <p className="au-now-sentence" data-testid="autonomy-sentence">
              <AskSentence level={level} ask={ask} largest={largest} />
            </p>
            <AskLine level={level} ask={ask} largest={largest} testId="autonomy-line" />
            {largest !== null && (
              <p className="au-now-note">
                {fill(t('largestNote'), {
                  largest: <MoneyFig value={largest} />,
                  link: (
                    <Link className="au-link" to={rowEnv === scope.active ? '/settings/money' : `/settings/money?env=${rowEnv}`}>
                      {t('moneyLink')}
                    </Link>
                  ),
                })}
              </p>
            )}
          </div>
        </Section>

        <Section id="au-levels-title" title={t('levels')} note={endsText ? t('firstMonthNote', { date: endsText }) : t('levelsNote')}>
          <div ref={levelsRef}>
            <AutonomyPicker
              value={choice}
              onChange={s.choose}
              enabled={enabledAutonomyLevels(doc)}
              savedLevel={level}
              busyLevel={s.busyLevel}
              extra={field}
              disabledReason={endsText ? t('firstMonth', { date: endsText }) : undefined}
            />
          </div>
        </Section>

        <Section id="au-always-title" title={t('always')}>
          <div className="st-group au-rows">
            {ALWAYS.map(([Icon, key]) => (
              <div key={key} className="au-row">
                <span className="au-row-icon" aria-hidden="true">
                  <Icon strokeWidth={1.75} />
                </span>
                <span className="au-row-label">{t(key)}</span>
              </div>
            ))}
            <Link to="/settings/notifications" className="au-row is-link">
              <span className="au-row-icon" aria-hidden="true">
                <Bell strokeWidth={1.75} />
              </span>
              <span className="au-row-label">{t('a4')}</span>
              <span className="au-row-value">{t('a4link')}</span>
              <ChevronRight aria-hidden="true" className="au-row-chev rtl:-scale-x-100" strokeWidth={2} />
            </Link>
          </div>
        </Section>

        <Section id="au-waits-title" title={t('waits')} note={t('waitsNote')}>
          <div className="st-group au-rows">
            <div className="au-row">
              <span className="au-row-label">{t('window')}</span>
              <span className="au-row-value is-strong">
                {fill(t('windowV'), {
                  n: (
                    <bdi dir="ltr" className="tabular-nums">
                      {formatFixed(timeout, 0, lang)}
                    </bdi>
                  ),
                })}
              </span>
            </div>
            <div className="au-row">
              <span className="au-row-label">{t('timeout')}</span>
              <span className="au-row-value is-strong">{t('timeoutV')}</span>
            </div>
          </div>
        </Section>

        <Section id="au-hist-title" title={t('history')} note={t('historyNote')}>
          <Changes entries={history.data?.data} loading={history.isPending} />
        </Section>

        {s.sheet && (
          <TurnOnSheet
            open
            level={s.sheet.level}
            raise={s.sheet.raise}
            mode="settings"
            initialAsk={s.sheet.initialAsk}
            largest={largest}
            pending={s.pending}
            fieldError={s.sheetError}
            notice={s.sheetNotice}
            onAskEdited={s.clearSheetError}
            onCancel={s.cancelSheet}
            onConfirm={s.confirmSheet}
          />
        )}
      </>
    )
  }

  return (
    <div className="st-page st-risk au-page">
      <PaneHeader scope={scope} version={doc && !query.isPending ? doc.version : null} />
      <EnvScopeNotice scope={scope} />
      {body}
    </div>
  )
}
