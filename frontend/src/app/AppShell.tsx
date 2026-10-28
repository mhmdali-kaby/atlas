import { useTranslation } from 'react-i18next'
import { Outlet } from 'react-router'

/**
 * Placeholder shell. The design-system and app-shell tasks replace the layout;
 * the permanent risk footer (brief §1.10) must stay on every screen.
 */
export function AppShell() {
  const { t } = useTranslation()
  const env = 'demo'
  return (
    <div className="flex min-h-dvh flex-col">
      <main className="flex-1">
        <Outlet />
      </main>
      <EnvFooter label={t('footer', { env: t(`env.${env}`) })} />
    </div>
  )
}

export function EnvFooter({ label }: { label: string }) {
  return (
    <footer role="contentinfo" className="px-6 py-2 text-caption text-label-secondary">
      {label}
    </footer>
  )
}
