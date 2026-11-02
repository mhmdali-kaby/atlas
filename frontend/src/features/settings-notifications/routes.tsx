import { Bell } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { NotificationsPage } from './NotificationsPage'

/* Settings → Notifications (brief §14.9, ATL-218): per-event channel switches and the Telegram bot. */
const settingsNotifications: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/notifications', element: <NotificationsPage /> }],
  nav: {
    path: '/settings/notifications',
    labelKey: 'settings-notifications:nav',
    order: 150,
    icon: Bell,
  },
}

export default settingsNotifications
