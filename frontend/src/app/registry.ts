import { buildShellRegistry, type FeatureModule } from './feature'

/* Every src/features/<name>/routes.tsx, found by glob (decision 233). */
const modules = import.meta.glob<{ default: FeatureModule }>('../features/*/routes.tsx', { eager: true })

export const features: FeatureModule[] = Object.values(modules).map((m) => m.default)

/** The area switcher and each area's sidebar sections: what the features registered. */
export const shellRegistry = buildShellRegistry(features)

/** Every sidebar link, in order. */
export const navEntries = shellRegistry.navEntries
