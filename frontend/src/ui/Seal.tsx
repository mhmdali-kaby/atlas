import { useMemo } from 'react'
import { cx } from './cx'

/*
 * Atlas's guilloché seal (design: core.js `ui.seal` / `ui.appIcon`). A rosette generated
 * from a seed string: the same seed always draws the same seal. Ported from the approved
 * prototype; this task uses it for the app icon and the faint ring behind sign-in.
 */

function hashStr(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function prng(seed: number): () => number {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let x = Math.imul(a ^ (a >>> 15), 1 | a)
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296
  }
}

const P = (x: number, y: number) => `${x.toFixed(2)} ${y.toFixed(2)}`

export type SealDetail = 'full' | 'mini'

interface SealGeometry {
  band: string[]
  rose: string[]
}

const cache = new Map<string, SealGeometry>()

function sealGeometry(seed: string, detail: SealDetail): SealGeometry {
  const key = `${seed}|${detail}`
  const hit = cache.get(key)
  if (hit) return hit
  const r = prng(hashStr(seed))
  const lobes = 13 + Math.floor(r() * 9)
  const rings = detail === 'mini' ? 3 : 7 + Math.floor(r() * 3)
  const amp = 3.0 + r() * 1.6
  const petals = 5 + Math.floor(r() * 5)
  const loopsK = 0.55 + r() * 0.35
  const spin = r() * Math.PI
  const band: string[] = []
  const bandSteps = detail === 'mini' ? 240 : 720
  for (let j = 0; j < rings; j++) {
    const ph = (j / rings) * Math.PI + spin
    let d = ''
    for (let i = 0; i <= bandSteps; i++) {
      const a = (i / bandSteps) * Math.PI * 2
      const rad = 37.5 + amp * Math.sin(lobes * a + ph)
      d += (i ? 'L' : 'M') + P(50 + rad * Math.cos(a), 50 + rad * Math.sin(a))
    }
    band.push(d)
  }
  const rose: string[] = []
  if (detail !== 'mini') {
    const copies = 4
    const steps = 900
    for (let c = 0; c < copies; c++) {
      const k = loopsK + c * 0.11
      const rot = spin + (c * Math.PI) / (petals * copies)
      let d = ''
      for (let i = 0; i <= steps; i++) {
        const a = (i / steps) * Math.PI * 2
        const R = petals
        const rr = 1
        const dd = 1 + k
        const x = (R + rr) * Math.cos(a) - dd * Math.cos(((R + rr) / rr) * a)
        const y = (R + rr) * Math.sin(a) - dd * Math.sin(((R + rr) / rr) * a)
        const s = 27 / (R + rr + dd)
        const xr = x * Math.cos(rot) - y * Math.sin(rot)
        const yr = x * Math.sin(rot) + y * Math.cos(rot)
        d += (i ? 'L' : 'M') + P(50 + xr * s, 50 + yr * s)
      }
      rose.push(d)
    }
  }
  const geometry = { band, rose }
  cache.set(key, geometry)
  return geometry
}

export interface SealProps {
  seed: string
  size: number
  detail?: SealDetail
  /** `band` draws only the woven ring (the sign-in backdrop). */
  variant?: 'full' | 'band'
  /** Ink class for every stroke; defaults to the seal inks. */
  inkClassName?: string
  className?: string
}

/** Decorative: always aria-hidden. */
export function Seal({ seed, size, detail = 'full', variant = 'full', inkClassName, className }: SealProps) {
  const g = useMemo(() => sealGeometry(seed, detail), [seed, detail])
  const ink = inkClassName ?? 'stroke-seal-ink-1'
  const thin = detail === 'mini' ? 'opacity-85' : 'opacity-[0.62]'
  return (
    <span aria-hidden="true" className={cx('inline-grid shrink-0 place-items-center', className)} style={{ width: size, height: size }}>
      <svg viewBox="0 0 100 100" className="size-full overflow-visible fill-none [&_*]:[vector-effect:non-scaling-stroke]">
        {variant === 'full' && (
          <>
            <circle cx="50" cy="50" r="48.6" className={cx(ink, 'stroke-[0.9px] opacity-55')} />
            {detail !== 'mini' && (
              <>
                <circle cx="50" cy="50" r="47.2" className={cx(ink, 'stroke-[0.5px] opacity-45')} />
                <circle cx="50" cy="50" r="42.2" className={cx(ink, 'stroke-[0.5px] opacity-45')} />
              </>
            )}
          </>
        )}
        <g className={cx(ink, thin, variant === 'band' ? 'stroke-[0.7px]' : 'stroke-[0.55px]')}>
          {g.band.map((d, i) => (
            <path key={i} d={d} />
          ))}
        </g>
        {variant === 'full' && (
          <>
            <g className={cx(inkClassName ?? 'stroke-seal-ink-2', 'stroke-[0.55px] opacity-70')}>
              {g.rose.map((d, i) => (
                <path key={i} d={d} />
              ))}
            </g>
            <circle cx="50" cy="50" r={detail === 'mini' ? 15 : 12.5} className={cx(ink, 'stroke-[0.8px] opacity-90')} />
          </>
        )}
      </svg>
    </span>
  )
}

/** The app icon: Atlas's own seal engraved in white on the deep-blue tile. */
export function AppIcon({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cx('app-icon-tile grid shrink-0 place-items-center overflow-hidden', className)}
      style={{ width: size, height: size, borderRadius: size * 0.24 }}
    >
      <Seal seed="atlas" size={size * 0.86} detail={size < 48 ? 'mini' : 'full'} inkClassName="stroke-on-solid" />
    </span>
  )
}
