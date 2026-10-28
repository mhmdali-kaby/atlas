import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

/*
 * src/ui uses logical properties only (brief §15, design §5), so RTL mirrors
 * for free. This fails the run on any physical-direction utility — ml-, mr-,
 * pl-, pr-, left-, right- (with any variant or a leading minus), text-left /
 * text-right — or a physical margin/padding/left/right declaration in CSS.
 * Use ms-/me-/ps-/pe-/start-/end-/text-start/text-end and inset-inline-*.
 */
const UI = resolve(__dirname)
const SELF = 'no-physical-classes.test.ts'
const SCANNED = /\.(ts|tsx|css)$/

const PHYSICAL_UTILITY = /(?<![\w-])-?(?:ml|mr|pl|pr|left|right)-[\w[\].%/-]+/g
const PHYSICAL_TEXT_ALIGN = /(?<![\w-])text-(?:left|right)(?![\w-])/g
const PHYSICAL_CSS = /(?<![\w-])(?:(?:margin|padding|border)-(?:left|right)|left|right)\s*:/g

export function findPhysical(text: string, { css = false } = {}): string[] {
  const hits = [...text.matchAll(PHYSICAL_UTILITY), ...text.matchAll(PHYSICAL_TEXT_ALIGN)]
  if (css) hits.push(...text.matchAll(PHYSICAL_CSS))
  return hits.map((m) => m[0])
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

describe('src/ui physical-direction guard', () => {
  it('detects physical utilities and declarations', () => {
    const bad = ['ml-2', 'mr-4', 'pl-3', 'pr-1', 'left-0', 'right-4', '-ml-2', 'hover:pr-2', 'rtl:-mr-1', 'text-left']
    for (const cls of bad) expect(findPhysical(`className="flex ${cls} gap-2"`), cls).toHaveLength(1)
    expect(findPhysical('.x { margin-left: 4px; right: 0 }', { css: true })).toHaveLength(2)
  })

  it('accepts logical utilities', () => {
    const good = 'ms-2 me-4 ps-3 pe-1 start-0 end-4 -ms-2 rtl:-scale-x-100 text-start text-end inset-x-0 border-separator'
    expect(findPhysical(`className="${good}"`)).toEqual([])
    expect(findPhysical('.x { inset-inline-start: 0; margin-inline-end: 4px }', { css: true })).toEqual([])
  })

  it('finds none in src/ui', () => {
    const offenders = walk(UI)
      .filter((path) => SCANNED.test(path) && !path.endsWith(SELF))
      .flatMap((path) => {
        const file = relative(UI, path).split(sep).join('/')
        return findPhysical(readFileSync(path, 'utf8'), { css: file.endsWith('.css') }).map((hit) => `${file}: ${hit}`)
      })
    expect(offenders).toEqual([])
  })
})
