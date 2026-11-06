import { CircleAlert, FileClock, History } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Sheet, toast } from '../../ui'
import { Notice } from '../onboarding'
import { useLang } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import { pinErrorText, usePinPrompt, type PromptVersion } from './prompts'

/**
 * "Use version N…" (design v22 §7): confirms, then PUT prompt-pin {version: N}. A 409 or no
 * answer keeps the sheet open with the reason; the list is refetched on 409.
 */
export function UsePromptSheet({
  slug,
  name,
  version,
  fileVersion,
  open,
  onOpenChange,
  onPinned,
}: {
  slug: string
  name: string
  version: PromptVersion
  fileVersion: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onPinned: (version: number) => void
}) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const pin = usePinPrompt(slug)
  const [error, setError] = useState<string | null>(null)
  const v = version.version

  const go = () => {
    setError(null)
    pin.mutate(
      { version: v },
      {
        onSuccess: () => {
          onOpenChange(false)
          onPinned(v)
          toast({ tone: 'success', title: t('pr.toast', { name, v }), body: t('pr.toastBody', { v }) })
        },
        onError: (err) => setError(pinErrorText(err, t)),
      },
    )
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) setError(null)
        onOpenChange(next)
      }}
      icon={<History strokeWidth={1.75} />}
      title={t('pr.sh.title', { v, name })}
      description={t('pr.sh.text', { v, name, n: fileVersion ?? v })}
      actions={
        <>
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={pin.isPending}>
            {t('pr.sh.cancel')}
          </Button>
          <Button variant="primary" loading={pin.isPending} onClick={go} data-testid="pin-confirm">
            {pin.isPending ? t('pr.sh.going') : t('pr.sh.go', { v })}
          </Button>
        </>
      }
    >
      <div className="pmt-sheet-fact">
        <FileClock aria-hidden="true" strokeWidth={1.75} />
        <span>{t('pr.v', { n: v })}</span>
        <span className="text-label-secondary tabular-nums">{formatDayTime(version.created_at, lang)}</span>
        <code dir="ltr">{version.hash12}</code>
      </div>
      {error && <Notice tone="error" icon={CircleAlert} role="alert" title={error} />}
    </Sheet>
  )
}
