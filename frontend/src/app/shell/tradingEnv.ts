/**
 * The environment Atlas trades in. Phase 0 is demo only (brief §1.4); the live switch
 * (Phase 4) will read it from the settings table and replace this constant.
 */
export type TradingEnv = 'demo' | 'live'

export function useTradingEnv(): TradingEnv {
  return 'demo'
}
