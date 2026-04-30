import type { Config } from 'tailwindcss'

/**
 * Colors reference CSS custom properties defined in `src/index.css` under
 * `:root` / `[data-theme="dark"]`. The `rgb(var(--color-X) / <alpha-value>)`
 * pattern lets Tailwind keep its alpha syntax (e.g. `bg-brand-500/20`)
 * working unchanged — Tailwind 3.3+ substitutes `<alpha-value>` with the
 * actual alpha at utility generation time.
 *
 * To add a new theme later:
 *   1. Add `[data-theme="light"] { --color-brand-500: ...; ... }` to index.css.
 *   2. Add a toggle in the UI that flips `<html data-theme="...">`.
 * Nothing in this config needs to change.
 */
const rgbVar = (name: string) => `rgb(var(--${name}) / <alpha-value>)`

const config: Config = {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  rgbVar('color-brand-50'),
          100: rgbVar('color-brand-100'),
          200: rgbVar('color-brand-200'),
          300: rgbVar('color-brand-300'),
          400: rgbVar('color-brand-400'),
          500: rgbVar('color-brand-500'),
          600: rgbVar('color-brand-600'),
          700: rgbVar('color-brand-700'),
          800: rgbVar('color-brand-800'),
          900: rgbVar('color-brand-900'),
        },
        // Semantic surfaces — for use in new components. Existing code that
        // uses `bg-gray-900` / `bg-gray-950` keeps working; migrate to these
        // when touching a component for other reasons.
        surface: {
          DEFAULT: rgbVar('color-surface'),
          raised:  rgbVar('color-surface-raised'),
          sunken:  rgbVar('color-surface-sunken'),
        },
        // Full gray ramp routed through CSS vars. Required for light-
        // theme support: toggling `<html data-theme="light">` flips each
        // `--color-gray-N` to its inverse, so `text-gray-100` becomes
        // dark-on-light and `bg-gray-900` becomes light-on-bg. All 87+
        // existing usages auto-switch without component changes.
        // Fractional utilities like `bg-gray-900/30` still work — the
        // `<alpha-value>` substitution happens before the var is
        // resolved.
        gray: {
          50:  rgbVar('color-gray-50'),
          100: rgbVar('color-gray-100'),
          200: rgbVar('color-gray-200'),
          300: rgbVar('color-gray-300'),
          400: rgbVar('color-gray-400'),
          500: rgbVar('color-gray-500'),
          600: rgbVar('color-gray-600'),
          700: rgbVar('color-gray-700'),
          800: rgbVar('color-gray-800'),
          900: rgbVar('color-gray-900'),
          950: rgbVar('color-gray-950'),
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}

export default config
