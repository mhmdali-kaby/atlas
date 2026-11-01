import { describe, expect, it } from 'vitest'
import { derivePorts, PORT_BASES, portOffset, RESERVED_PORTS } from './stack'

/**
 * Two worktrees running Playwright at once must not collide on ports (ATL-187), the same way they
 * already don't collide on the test database (stack.ts DATABASE, decision #252). These tests pin
 * down the derivation so the two checkouts below, and any real one, land on different, non-reserved
 * ports, and so explicit env overrides still win.
 */

const repoA = '/Users/dev/Work/atlas/wt-task-atl-187'
const repoB = '/Users/dev/Work/atlas/wt-task-atl-200'

describe('portOffset', () => {
  it('is deterministic for the same repo root', () => {
    expect(portOffset(repoA)).toBe(portOffset(repoA))
  })

  it('differs for different repo roots', () => {
    expect(portOffset(repoA)).not.toBe(portOffset(repoB))
  })

  it('never produces a base+offset that lands on a reserved port, for many roots', () => {
    for (let i = 0; i < 500; i++) {
      const offset = portOffset(`/checkouts/sample-${i}`)
      for (const base of Object.values(PORT_BASES)) {
        expect(RESERVED_PORTS).not.toContain(base + offset)
      }
    }
  })
})

describe('derivePorts', () => {
  it('gives the same port set for the same repo root', () => {
    expect(derivePorts(repoA, {})).toEqual(derivePorts(repoA, {}))
  })

  it('gives a different port set for a different repo root', () => {
    expect(derivePorts(repoA, {})).not.toEqual(derivePorts(repoB, {}))
  })

  it('never equals a reserved port on any field', () => {
    for (const ports of [derivePorts(repoA, {}), derivePorts(repoB, {})]) {
      for (const port of Object.values(ports)) {
        expect(RESERVED_PORTS).not.toContain(port)
      }
    }
  })

  it('stays within [base, base + 2990] for every field', () => {
    const offset = portOffset(repoA)
    const ports = derivePorts(repoA, {})
    expect(offset).toBeGreaterThanOrEqual(0)
    expect(offset).toBeLessThanOrEqual(2990)
    expect(ports.mocked).toBe(PORT_BASES.mocked + offset)
    expect(ports.spa).toBe(PORT_BASES.spa + offset)
    expect(ports.api).toBe(PORT_BASES.api + offset)
    expect(ports.core).toBe(PORT_BASES.core + offset)
    expect(ports.reverb).toBe(PORT_BASES.reverb + offset)
  })

  it('lets an explicit env var override the derived mocked/api ports', () => {
    const ports = derivePorts(repoA, { E2E_PORT: '19999', E2E_API_PORT: '29999' })
    expect(ports.mocked).toBe(19999)
    expect(ports.api).toBe(29999)
    // Untouched fields still come from the derivation.
    const offset = portOffset(repoA)
    expect(ports.spa).toBe(PORT_BASES.spa + offset)
    expect(ports.core).toBe(PORT_BASES.core + offset)
    expect(ports.reverb).toBe(PORT_BASES.reverb + offset)
  })

  it('lets every port be overridden individually', () => {
    const env = {
      E2E_PORT: '11111',
      E2E_FLOW_PORT: '22222',
      E2E_API_PORT: '33333',
      E2E_CORE_PORT: '44444',
      E2E_REVERB_PORT: '55555',
    }
    expect(derivePorts(repoA, env)).toEqual({ mocked: 11111, spa: 22222, api: 33333, core: 44444, reverb: 55555 })
  })
})
