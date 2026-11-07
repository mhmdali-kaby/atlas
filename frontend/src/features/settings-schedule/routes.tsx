import { CalendarClock } from 'lucide-react'
import type { FeatureModule } from '../../app/feature'
import { SchedulePage } from './SchedulePage'

/* Settings → Schedule (decision 255 (2), ATL-230): job times, analysis slots, quiet hours, weekend stock perps. */
const settingsSchedule: FeatureModule = {
  area: 'settings',
  routes: [{ path: 'settings/schedule', element: <SchedulePage /> }],
  nav: { path: '/settings/schedule', labelKey: 'settings-schedule:nav', order: 155, icon: CalendarClock },
}

export default settingsSchedule
