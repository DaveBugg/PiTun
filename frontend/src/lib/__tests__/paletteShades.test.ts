/**
 * Tailwind emits nothing for a colour shade the theme doesn't define, and it
 * does so silently. `dark:bg-brand-950/30` therefore vanished at build time and
 * the light background underneath it stayed — a white block in dark mode, on
 * five elements, shipped unnoticed because nothing errors and the class simply
 * isn't in the CSS. A second one left a card with no background at all.
 *
 * This walks the source for shade suffixes and checks each against the ramps
 * declared in `index.css`, so the next one fails here instead of on a screen.
 *
 * Files are read with `node:fs`, not `import.meta.glob`: an eager glob
 * *imports* what it matches, which would execute every other test file in the
 * suite. We only want the text.
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(process.cwd(), 'src')
const CSS = join(SRC, 'index.css')

// Ramps we define ourselves. Tailwind's built-in colours (amber, emerald, red…)
// carry their own full scale and are not our problem.
const OWN_RAMPS = ['brand', 'gray'] as const

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry: string) => {
    const p = join(dir, entry)
    // Tests discuss shades in prose; only shipped code emits classes.
    if (statSync(p).isDirectory()) return entry === '__tests__' ? [] : walk(p)
    return /\.tsx?$/.test(entry) ? [p] : []
  })
}

function declaredShades(ramp: string, css: string): Set<string> {
  const found = new Set<string>()
  for (const m of css.matchAll(new RegExp(`--color-${ramp}-(\\d+)\\s*:`, 'g'))) {
    found.add(m[1])
  }
  return found
}

describe('colour shades used in the UI exist in the theme', () => {
  const css = readFileSync(CSS, 'utf8')
  const files = walk(SRC)

  it('reads the theme and the sources', () => {
    expect(css.length).toBeGreaterThan(0)
    expect(files.length).toBeGreaterThan(10)
  })

  for (const ramp of OWN_RAMPS) {
    it(`${ramp}: every shade referenced is declared`, () => {
      const declared = declaredShades(ramp, css)
      expect(declared.size).toBeGreaterThan(0)

      const offenders: string[] = []
      for (const file of files) {
        const text = readFileSync(file, 'utf8')
        for (const m of text.matchAll(new RegExp(`\\b${ramp}-(\\d{2,4})\\b`, 'g'))) {
          if (!declared.has(m[1])) {
            offenders.push(`${file.slice(SRC.length + 1)}: ${ramp}-${m[1]}`)
          }
        }
      }
      expect(offenders).toEqual([])
    })
  }
})
