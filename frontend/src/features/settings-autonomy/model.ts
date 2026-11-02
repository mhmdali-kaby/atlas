import type { SettingsDoc } from '../onboarding'

/** The settings key of the "Ask me above" amount (ATL-212). */
export const ASK_FIELD = 'autonomy_ask_above_usd'

export const savedAsk = (doc: SettingsDoc): string => String(doc.settings[ASK_FIELD] ?? '0')
