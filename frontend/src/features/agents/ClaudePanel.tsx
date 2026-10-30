import { CircleAlert, CircleCheck, CircleMinus, CircleX, CloudOff, Copy, ExternalLink, RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Skeleton, toast } from '../../ui'
import { formatClock } from '../portfolio/time'
import { useLang } from '../portfolio/hooks'
import { useClaudeStatus, type ClaudeStatus } from './api'
import { claudeView, cliVersion, keyVars, type ClaudeView } from './model'
import { NS, Tr } from './parts'

/*
 * The Claude CLI panel (design v9 agents.js claudePanel; decision 242): agents run on the owner's
 * logged-in Claude Code, so this check gates them all and leads the page. Fetched on open and on
 * "Check again" only — the endpoint runs the CLI every call.
 */

const INSTALL_URL = 'https://docs.claude.com/en/docs/claude-code/setup'

function Checks({ c }: { c: ClaudeStatus }) {
  const { t } = useTranslation(NS)
  const version = cliVersion(c)
  const items: { s: 'pass' | 'fail' | 'skip'; text: React.ReactNode }[] = [
    c.installed
      ? { s: 'pass', text: version ? <Tr k="chk.cliPass" values={{ version }} /> : t('chk.cliPassNoVersion') }
      : { s: 'fail', text: t('chk.cliFail') },
    !c.installed ? { s: 'skip', text: t('chk.loginSkip') } : c.logged_in ? { s: 'pass', text: t('chk.loginPass') } : { s: 'fail', text: t('chk.loginFail') },
    c.api_key_env_clear ? { s: 'pass', text: t('chk.keyPass') } : { s: 'fail', text: <Tr k="chk.keyFail" values={{ var: keyVars(c).join(', ') }} /> },
  ]
  const Icon = { pass: CircleCheck, fail: CircleX, skip: CircleMinus }
  return (
    <ul className="ag-checks" aria-label={t('cl.checks')}>
      {items.map(({ s, text }, i) => {
        const I = Icon[s]
        return (
          <li key={i} className={`is-${s}`}>
            <I strokeWidth={2} aria-hidden="true" />
            <span className="sr-only">{t(`chk.${s}`)}: </span>
            <span>{text}</span>
          </li>
        )
      })}
    </ul>
  )
}

export function ClaudePanel() {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const q = useClaudeStatus()
  const checking = q.isFetching
  const lastView = useRef<ClaudeView | null>(null)
  const c = q.data?.data

  // A re-check that turns a problem into "ready" says so once (design: toast "Claude is ready").
  useEffect(() => {
    if (!c) return
    const view = claudeView(c)
    if (lastView.current && lastView.current !== 'ok' && view === 'ok') {
      toast({ tone: 'success', title: t('cl.readyToast'), body: t('cl.readyToastBody') })
    }
    lastView.current = view
  }, [c, t])

  const recheck = () => void q.refetch()
  const checkBtn = (
    <Button size="sm" variant="secondary" className="ag-check" onClick={recheck} loading={checking} icon={checking ? undefined : <RefreshCw />}>
      {t(checking ? 'cl.checking' : 'cl.check')}
    </Button>
  )

  if (q.isPending) {
    return (
      <section className="ag-group ag-claude" aria-busy="true" data-testid="ag-claude-loading">
        <div className="ag-cl-head">
          <Skeleton style={{ width: 36, height: 36, borderRadius: 10 }} />
          <span className="grid gap-2">
            <Skeleton style={{ width: 240, height: 15, maxWidth: '100%' }} />
            <Skeleton style={{ width: 360, height: 12, maxWidth: '100%' }} />
          </span>
        </div>
      </section>
    )
  }

  if (!c) {
    return (
      <section className="ag-group ag-claude is-error" aria-labelledby="ag-cl-title" data-testid="ag-claude" data-view="failed">
        <div className="ag-cl-head">
          <span className="ag-cl-icon is-error" aria-hidden="true">
            <CloudOff strokeWidth={1.75} />
          </span>
          <div className="ag-cl-text" aria-live="polite">
            <h2 className="ag-cl-title" id="ag-cl-title">
              {t('cl.failTitle')}
            </h2>
            <p className="ag-cl-sub">{t('cl.failBody')}</p>
          </div>
          <Button size="sm" variant="secondary" onClick={recheck} loading={checking} icon={checking ? undefined : <RefreshCw />}>
            {t(checking ? 'cl.checking' : 'cl.retry')}
          </Button>
        </div>
      </section>
    )
  }

  const view = claudeView(c)
  const version = cliVersion(c)
  const checked = (
    <span className="ag-cl-at">
      {t('cl.checkedAt', { time: formatClock(c.checked_at, lang, false) })}
    </span>
  )

  if (view === 'ok') {
    return (
      <section className="ag-group ag-claude is-ok" aria-labelledby="ag-cl-title" data-testid="ag-claude" data-view="ok">
        <div className="ag-cl-head">
          <span className="ag-cl-icon is-ok" aria-hidden="true">
            <CircleCheck strokeWidth={1.75} />
          </span>
          <div className="ag-cl-text" aria-live="polite">
            <h2 className="ag-cl-title" id="ag-cl-title">
              {t('cl.okTitle')}
            </h2>
            <p className="ag-cl-sub">
              {version ? <Tr k="cl.okBody" values={{ version }} /> : t('cl.okBodyNoVersion')} {checked}
            </p>
          </div>
          {checkBtn}
        </div>
      </section>
    )
  }

  const tone = view === 'logged_out' ? 'warn' : 'error'
  const Icon = view === 'logged_out' ? TriangleAlert : CircleAlert
  const title = t(view === 'missing' ? 'cl.missTitle' : view === 'logged_out' ? 'cl.outTitle' : 'cl.keyTitle')
  const body =
    view === 'missing' ? <Tr k="cl.missBody" /> : view === 'logged_out' ? t('cl.outBody') : <Tr k="cl.keyBody" values={{ var: keyVars(c).join(', ') }} />
  const command = c.login_command || 'claude login'

  const copy = () => {
    try {
      void navigator.clipboard?.writeText(command)
    } catch {
      /* clipboard refused: the command is still on screen to select */
    }
    toast({ tone: 'success', title: t('cl.copied') })
  }

  return (
    <section className={`ag-group ag-claude is-${tone}`} aria-labelledby="ag-cl-title" data-testid="ag-claude" data-view={view}>
      <div className="ag-cl-head">
        <span className={`ag-cl-icon is-${tone}`} aria-hidden="true">
          <Icon strokeWidth={1.75} />
        </span>
        <div className="ag-cl-text" aria-live="polite">
          <h2 className="ag-cl-title" id="ag-cl-title">
            {title}
          </h2>
          <p className="ag-cl-sub">{body}</p>
        </div>
        {checkBtn}
      </div>
      {view === 'logged_out' && (
        <div className="ag-cmd">
          <code dir="ltr" data-testid="ag-login-command">
            {command}
          </code>
          <Button size="sm" variant="plain" icon={<Copy />} onClick={copy} aria-label={t('cl.copyLabel')}>
            {t('cl.copy')}
          </Button>
        </div>
      )}
      {view === 'missing' && (
        <p className="ag-cl-more">
          <a className="ag-link" href={INSTALL_URL} target="_blank" rel="noopener noreferrer">
            {t('cl.install')}
            <ExternalLink strokeWidth={1.75} aria-hidden="true" className="rtl:-scale-x-100" />
          </a>
        </p>
      )}
      <div className="ag-cl-foot">
        <Checks c={c} />
        {checked}
      </div>
    </section>
  )
}
