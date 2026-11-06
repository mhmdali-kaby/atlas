import { FlaskConical, RadioTower } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { useTradingEnv, type TradingEnv } from '../../app/shell/tradingEnv'
import { SegmentedControl } from '../../ui'
import { Notice } from '../onboarding'
import './env-scope.css'

/*
 * "Settings for: Demo | Live" (ATL-242; design v21 js/live.js `scopeControl`/`scopeNotice`).
 * Settings are one versioned row per env, so Risk, Money limits, Autonomy and Markets let the
 * owner edit the live row while Atlas still trades on demo (the pre-flight needs a live risk
 * profile, autonomy ≤ 2 and a capital cap first). `?env=live|demo` selects the row; with no
 * param the pane shows the env Atlas trades on.
 */

export type ScopeEnv = TradingEnv

export interface EnvScope {
  /** The settings row this pane reads and saves. */
  env: ScopeEnv
  /** The env Atlas trades on. */
  active: ScopeEnv
  setEnv: (env: ScopeEnv) => void
}

const isEnv = (v: string | null): v is ScopeEnv => v === 'demo' || v === 'live'

export function useEnvScope(): EnvScope {
  const active = useTradingEnv()
  const [params, setParams] = useSearchParams()
  const q = params.get('env')
  const env = isEnv(q) ? q : active
  const setEnv = (next: ScopeEnv) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        p.set('env', next)
        return p
      },
      { replace: true },
    )
  return { env, active, setEnv }
}

/** The pane header's control: "Settings for  [● Demo | ● Live]". */
export function EnvScopeControl({ scope, disabled }: { scope: EnvScope; disabled?: boolean }) {
  const { t } = useTranslation('settings')
  return (
    <div className="lv-scope" data-testid="env-scope">
      <span className="lv-scope-label" aria-hidden="true">
        {t('scope.label')}
      </span>
      <SegmentedControl<ScopeEnv>
        aria-label={t('scope.label')}
        className="lv-scope-seg"
        value={scope.env}
        disabled={disabled}
        onValueChange={scope.setEnv}
        options={(['demo', 'live'] as const).map((e) => ({
          value: e,
          label: (
            <span className="lv-scope-opt">
              <span className={`lv-dot is-${e}`} aria-hidden="true" />
              {t(`scope.${e}`)}
            </span>
          ),
        }))}
      />
    </div>
  )
}

/** Under the pane header while the pane edits the env Atlas is not trading on. */
export function EnvScopeNotice({ scope }: { scope: EnvScope }) {
  const { t } = useTranslation('settings')
  if (scope.env === scope.active) return null
  return scope.env === 'live' ? (
    <Notice tone="info" icon={RadioTower} role="status" className="lv-notice is-live" data-testid="env-scope-notice" title={t('scope.editLive')} />
  ) : (
    <Notice tone="info" icon={FlaskConical} role="status" className="lv-notice is-demo" data-testid="env-scope-notice" title={t('scope.editDemo')} />
  )
}
