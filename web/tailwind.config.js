/** @type {import('tailwindcss').Config} */
// Every colour is a CSS variable from src/styles/tokens.css so the theme lives in
// one place. Spacing stays on Tailwind's 4 px scale but layouts use 8 px steps
// (2, 4, 6, 8 ...) by convention.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: v('bg'),
        s1: v('s1'),
        s2: v('s2'),
        s3: v('s3'),
        line: v('line'),
        rule: v('rule'),
        ink: v('ink'),
        ink2: v('ink2'),
        dim: v('dim'),
        faint: v('faint'),
        accent: v('accent'),
        'accent-ink': v('accent-ink'),
        crit: v('crit'),
        high: v('high'),
        med: v('med'),
        low: v('low'),
        ok: v('ok'),
        fam1: v('fam1'),
        fam2: v('fam2'),
        fam3: v('fam3'),
        fam0: v('fam0'),
      },
      fontFamily: {
        sans: ['"Segoe UI"', '"Segoe UI Variable Text"', '"Nirmala UI"', '"Noto Sans Devanagari"', 'Mangal', 'system-ui', 'sans-serif'],
        mono: ['"Cascadia Mono"', 'Consolas', '"Segoe UI Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        micro: ['11.5px', '16px'],
        xs: ['12.5px', '16px'],
        sm: ['13.5px', '20px'],
        base: ['14.5px', '22px'],
        lg: ['16px', '24px'],
        xl: ['20px', '28px'],
        '2xl': ['24px', '32px'],
        num: ['32px', '36px'],
        hero: ['44px', '48px'],
      },
      borderRadius: { DEFAULT: '3px', sm: '2px', md: '4px' },
      screens: { tab: '1024px', lap: '1280px', wide: '1600px' },
    },
  },
  plugins: [],
}
