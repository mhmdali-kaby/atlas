import type { FeatureModule } from '../../app/feature'
import { LoginPage } from './LoginPage'

const auth: FeatureModule = {
  routes: [],
  publicRoutes: [{ path: '/login', element: <LoginPage /> }],
}

export default auth
