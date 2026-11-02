import type { FeatureModule } from '../../app/feature'
import { AgentList, ThreadList } from './SidebarSections'
import { ReplayPage, ReplayRedirect } from './ReplayPage'
import { WarRoomPage } from './WarRoomPage'

/*
 * The War Room (brief §12; design v4 War Room). /warroom is Main; /warroom/threads/<id> opens any
 * thread; /warroom/replay/<YYYY-MM-DD> replays a finished Baghdad day read-only (ATL-206). It is the signed-in home: HomeRedirect opens the first area, and this one comes first.
 */
const warroom: FeatureModule = {
  area: 'warroom',
  routes: [
    { path: 'warroom', element: <WarRoomPage /> },
    { path: 'warroom/threads/:threadId', element: <WarRoomPage /> },
    { path: 'warroom/replay', element: <ReplayRedirect /> },
    { path: 'warroom/replay/:date', element: <ReplayPage /> },
  ],
  sidebar: [
    { id: 'agents', order: 10, component: AgentList },
    { id: 'threads', order: 20, labelKey: 'warroom:sidebar.threads', component: ThreadList },
  ],
}

export default warroom
