import { CircleAlert, CircleCheck, CircleMinus, CircleX, Clock, CloudOff, Copy, ExternalLink, RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Skeleton, Spinner, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { formatClock } from '../portfolio/time'
import { useLang } from '../portfolio/hooks'
import { useClaudeStatus } from '../agents/api'
import { claudeView, cliVersion, keyVars, type ClaudeView } from '../agents/model'
import { NS as AGENTS_NS, Tr } from '../agents/parts'
import { Notice } from './Notice'
import { StepHead, StepMain, type StepProps } from './steps'
import './onboarding-n.css'

/*
 * Claude (design v21 onboarding.js claude(); decision #242): the agents run on the owner's
 * logged-in Claude Code, so this step runs the same GET /api/agents/claude-status the Agents page
 * and the doctor run. Three checks, then the one fix that's needed, and Check again. The owner may
 * carry on without it ("Set up later" in the footer); the agents stay offline until it passes.
 * The endpoint runs the CLI on every call, so it is fetched on open and on Check again only.
 */

const INSTALL_URL = 'https://docs.claude.com/en/docs/claude-code/setup'

type CheckState = 'pass' | 'fail' | 'skip' | 'unknown'

/** Whether the check passes: Continue needs it, and "Set up later" shows without it. */
export function useClaudeReady(enabled: boolean): { ready: boolean; checking: boolean } {
  const q = useClaudeStatus(enabled)
  const c = q.data?.data
  return { ready: !!c && claudeView(c) === 'ok' && !q.isFetching, checking: q.isFetching }
}

export function ClaudeStep({ autoFocus }: StepProps) {
  const { t } = useTranslation('onboarding')
  const { t: ta } = useTranslation(AGENTS_NS)
  const lang = useLang()
  const q = useClaudeStatus()
  const checking = q.isFetching
  const c = q.data?.data
  const lastView = useRef<ClaudeView | null>(null)

  // A re-check that turns a problem into "ready" says so once (the Agents page's toast).
  useEffect(() => {
    if (!c) return
    const view = claudeView(c)
    if (lastView.current && lastView.current !== 'ok' && view === 'ok') {
      toast({ tone: 'success', title: ta('cl.readyToast'), body: ta('cl.readyToastBody') })
    }
    lastView.current = view
  }, [c, ta])

  const recheck = () => void q.refetch()
  const head = <StepHead autoFocus={autoFocus} title={t('claude.title')} lead={t('claude.lead')} />
  const ready = !!c && claudeView(c) === 'ok' && !checking
  const later = !ready && (
    <p className="ob-carry ob-later-note" data-testid="claude-later-note">
      <Clock aria-hidden="true" strokeWidth={1.5} />
      <span>{t('claude.laterNote')}</span>
    </p>
  )

  if (q.isPending) {
    return (
      <>
        {head}
        <StepMain>
          <div aria-busy="true" aria-label={t('claude.checking')} data-testid="claude-loading">
            <Skeleton className="h-[196px] rounded-card" />
          </div>
        </StepMain>
      </>
    )
  }

  if (!c) {
    return (
      <>
        {head}
        <StepMain>
          <Notice
            tone="error"
            role="alert"
            icon={CloudOff}
            title={ta('cl.failTitle')}
            data-testid="claude-failed"
            actions={
              <Button size="sm" variant="secondary" onClick={recheck} loading={checking} icon={checking ? undefined : <RefreshCw />} data-testid="claude-check">
                {ta(checking ? 'cl.checking' : 'cl.retry')}
              </Button>
            }
          >
            {ta('cl.failBody')}
          </Notice>
          {later}
        </StepMain>
      </>
    )
  }

  const view = claudeView(c)
  const version = cliVersion(c)
  const rows: { k: 'cli' | 'login' | 'key'; s: CheckState; text: ReactNode }[] = [
    c.installed
      ? { k: 'cli', s: 'pass', text: version ? <Tr k="chk.cliPass" values={{ version }} /> : ta('chk.cliPassNoVersion') }
      : { k: 'cli', s: 'fail', text: ta('chk.cliFail') },
    !c.installed
      ? { k: 'login', s: 'skip', text: ta('chk.loginSkip') }
      : c.logged_in === null
        ? { k: 'login', s: 'unknown', text: ta('chk.loginUnknown') }
        : c.logged_in
          ? { k: 'login', s: 'pass', text: ta('chk.loginPass') }
          : { k: 'login', s: 'fail', text: ta('chk.loginFail') },
    c.api_key_env_clear
      ? { k: 'key', s: 'pass', text: ta('chk.keyPass') }
      : { k: 'key', s: 'fail', text: <Tr k="chk.keyFail" values={{ var: keyVars(c).join(', ') }} /> },
  ]
  const Icon = { pass: CircleCheck, fail: CircleX, skip: CircleMinus, unknown: CircleAlert }
  const command = c.login_command || 'claude login'
  const copy = () => {
    try {
      void navigator.clipboard?.writeText(command)
    } catch {
      /* clipboard refused: the command is still on screen to select */
    }
    toast({ tone: 'success', title: ta('cl.copied') })
  }

  return (
    <>
      {head}
      <StepMain>
        <div className="ob-checks-box" data-testid="claude-checks" data-view={checking ? 'checking' : view}>
          <ul className="ob-checks" aria-label={ta('cl.checks')} aria-busy={checking}>
            {rows.map(({ k, s, text }) => {
              const I = Icon[s]
              const shown = checking ? 'checking' : s
              return (
                <li key={k} className={cx('ob-check', `is-${shown}`)} data-check={k} data-state={shown}>
                  <span className="ob-check-icon" aria-hidden="true">
                    {checking ? <Spinner /> : <I strokeWidth={1.9} />}
                  </span>
                  <span className="ob-check-text">
                    <span className="ob-check-title">
                      <span className="sr-only">{checking ? t('claude.checking') : ta(`chk.${s}`)}: </span>
                      {text}
                    </span>
                    <span className="ob-check-sub">{t(`claude.sub.${k}.${s === 'unknown' ? 'fail' : s}`)}</span>
                  </span>
                </li>
              )
            })}
          </ul>
          <div className="ob-checks-foot">
            <span aria-live="polite">{checking ? t('claude.checking') : ta('cl.checkedAt', { time: formatClock(c.checked_at, lang, false) })}</span>
            <Button size="sm" variant="secondary" onClick={recheck} loading={checking} icon={checking ? undefined : <RefreshCw />} data-testid="claude-check">
              {ta(checking ? 'cl.checking' : 'cl.check')}
            </Button>
          </div>
        </div>

        {!checking && view === 'missing' && (
          <Notice
            tone="error"
            icon={CircleAlert}
            title={ta('cl.missTitle')}
            className="ob-fix"
            data-testid="claude-fix"
            actions={
              <a className="ob-fix-link" href={INSTALL_URL} target="_blank" rel="noopener noreferrer">
                {ta('cl.install')}
                <ExternalLink strokeWidth={1.75} aria-hidden="true" className="rtl:-scale-x-100" />
              </a>
            }
          >
            <Tr k="cl.missBody" />
          </Notice>
        )}
        {!checking && (view === 'logged_out' || view === 'login_unknown') && (
          <Notice
            tone="warning"
            icon={TriangleAlert}
            title={ta(view === 'logged_out' ? 'cl.outTitle' : 'cl.unknownTitle')}
            className="ob-fix"
            data-testid="claude-fix"
            actions={
              <div className="ob-cmd">
                <code dir="ltr" data-testid="claude-login-command">
                  {command}
                </code>
                <Button size="sm" variant="plain" icon={<Copy />} onClick={copy} aria-label={ta('cl.copyLabel')}>
                  {ta('cl.copy')}
                </Button>
              </div>
            }
          >
            {ta(view === 'logged_out' ? 'cl.outBody' : 'cl.unknownBody')}
          </Notice>
        )}
        {!checking && view === 'api_key' && (
          <Notice tone="error" icon={CircleAlert} title={ta('cl.keyTitle')} className="ob-fix" data-testid="claude-fix">
            <Tr k="cl.keyBody" values={{ var: keyVars(c).join(', ') }} />
          </Notice>
        )}
        {later}
      </StepMain>
    </>
  )
}

