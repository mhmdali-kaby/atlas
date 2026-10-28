import { CircleAlert, Plug, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../ui'
import { KeysSheet } from './sheets'

/* The page's empty and error states: no demo connection, and GET /api/connections failing. */

export function NotConnected({ focusOnMount }: { focusOnMount: boolean }) {
  const { t } = useTranslation('settings')
  const [open, setOpen] = useState(false)
  const [opened, setOpened] = useState(0)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (focusOnMount) button.current?.focus()
  }, [focusOnMount])

  return (
    <section className="st-group st-empty" aria-labelledby="st-none-title" data-testid="not-connected">
      <span className="st-empty-icon" aria-hidden="true">
        <Plug strokeWidth={1.5} />
      </span>
      <h2 id="st-none-title" className="text-title-3">
        {t('conn.none.title')}
      </h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('conn.none.body')}</p>
      <Button
        ref={button}
        variant="primary"
        icon={<Plug />}
        className="mt-2"
        onClick={() => {
          setOpened((n) => n + 1)
          setOpen(true)
        }}
      >
        {t('conn.connect')}
      </Button>
      <KeysSheet key={opened} open={open} onOpenChange={setOpen} connection={null} />
    </section>
  )
}

export function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings')
  const parts = t('conn.loadError.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="connections-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('conn.loadError.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code dir="ltr" className="rounded-[6px] bg-fill-3 px-1.5 text-label [unicode-bidi:isolate]">
              ./atlas up
            </code>
            {parts[1]}
          </>
        ) : (
          t('conn.loadError.body')
        )}
      </p>
      <Button variant="secondary" icon={<RefreshCw />} className="mt-2" onClick={onRetry}>
        {t('conn.loadError.retry')}
      </Button>
    </section>
  )
}
