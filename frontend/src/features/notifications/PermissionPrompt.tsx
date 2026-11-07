import { BellOff, MonitorDot } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, toast } from '../../ui'
import { useBrowserPermission } from './browser'

/** "Get alerts when Atlas is in the background": only while permission is undecided, never on load. */
export function PermissionPrompt() {
  const { t } = useTranslation('notifications')
  const { permission, hidden, ask, hide } = useBrowserPermission()
  const [asking, setAsking] = useState(false)
  if (hidden || permission === 'granted' || permission === 'unsupported') return null
  if (permission === 'denied') {
    return (
      <div className="nt-perm is-denied" role="note" data-testid="nt-perm">
        <BellOff aria-hidden="true" strokeWidth={1.5} />
        <div>
          <p className="nt-perm-title">{t('perm.denied.title')}</p>
          <p className="nt-perm-body">{t('perm.denied.body')}</p>
        </div>
        <Button variant="plain" size="sm" onClick={hide}>
          {t('perm.hide')}
        </Button>
      </div>
    )
  }
  return (
    <div className="nt-perm" role="note" data-testid="nt-perm">
      <MonitorDot aria-hidden="true" strokeWidth={1.5} />
      <div>
        <p className="nt-perm-title">{t('perm.title')}</p>
        <p className="nt-perm-body">{t('perm.body')}</p>
        <div className="nt-perm-actions">
          <Button
            variant="primary"
            size="sm"
            loading={asking}
            onClick={async () => {
              setAsking(true)
              try {
                const answer = await ask()
                if (answer === 'granted') toast({ title: t('perm.okToast'), body: t('perm.okBody') })
                else if (answer === 'denied') toast({ title: t('perm.noToast'), body: t('perm.noBody'), tone: 'info' })
              } finally {
                setAsking(false)
              }
            }}
          >
            {asking ? t('perm.asking') : t('perm.allow')}
          </Button>
          <Button variant="plain" size="sm" onClick={hide}>
            {t('perm.later')}
          </Button>
        </div>
      </div>
    </div>
  )
}
