import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

/*
 * src/styles/tokens.css is the only file under src/ allowed to declare a raw
 * colour (brief §15, ATL-14). Everything else uses the semantic tokens
 * (bg-background, text-label, var(--label) …).
 */
const SRC = resolve(__dirname, '..')
const ALLOWED = new Set(['styles/tokens.css'])
const SCANNED = /\.(css|ts|tsx|js|jsx|json|html|svg|mdx?)$/

// #rgb, #rgba, #rrggbb, #rrggbbaa — not followed by more word characters.
const HEX = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/gi
const FUNCTIONAL = /\b(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\(/gi

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

// Tests and play functions compare against computed styles, which browsers
// report as rgb(); they may name rgb()/rgba(), but never a hex.
const ASSERTS_COMPUTED = /\.(test|stories)\.tsx?$/

export function findRawColours(text: string, { allowFunctional = false } = {}): string[] {
  const hits = [...text.matchAll(HEX)]
  if (!allowFunctional) hits.push(...text.matchAll(FUNCTIONAL))
  return hits.map((m) => m[0])
}

describe('raw colours', () => {
  it('detects hex and functional colour literals', () => {
    expect(findRawColours(`color: ${'#'}fff; b: ${'#'}0A84FF; c: ${'rgba'}(0,0,0,.1)`)).toHaveLength(3)
    expect(findRawColours(`${'rgba'}(0,0,0,.1) and ${'#'}000`, { allowFunctional: true })).toEqual([`${'#'}000`])
    expect(findRawColours(`getElementById('root'); href="${'#'}main"; id="${'#'}add-row"`)).toHaveLength(0)
  })

  it('appear nowhere under src/ except styles/tokens.css', () => {
    const offenders = walk(SRC)
      .filter((path) => SCANNED.test(path))
      .map((path) => ({ file: relative(SRC, path).split(sep).join('/'), path }))
      .filter(({ file }) => !ALLOWED.has(file))
      .flatMap(({ file, path }) =>
        findRawColours(readFileSync(path, 'utf8'), { allowFunctional: ASSERTS_COMPUTED.test(file) }).map(
          (hit) => `${file}: ${hit}`,
        ),
      )
    expect(offenders).toEqual([])
  })

  it('tokens.css really is the file that holds them', () => {
    expect(findRawColours(readFileSync(join(SRC, 'styles/tokens.css'), 'utf8')).length).toBeGreaterThan(50)
  })
})
