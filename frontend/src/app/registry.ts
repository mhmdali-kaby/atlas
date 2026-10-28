import { collectNavEntries, type FeatureModule } from './feature'

/* Every src/features/<name>/routes.tsx, found by glob (decision 233). */
const modules = import.meta.glob<{ default: FeatureModule }>('../features/*/routes.tsx', { eager: true })

export const features: FeatureModule[] = Object.values(modules).map((m) => m.default)

/** The sidebar, in order: what the features registered. */
export const navEntries = collectNavEntries(features)
