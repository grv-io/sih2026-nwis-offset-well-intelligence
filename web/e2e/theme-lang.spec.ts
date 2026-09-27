import { expect, test, type Page } from '@playwright/test'

// Light theme + Hindi passes over every judge-facing screen (docs/DEMO_SCRIPT.md).
// Run against the demo server like smoke.spec.ts.

const SCREENS: { name: string; url: string; ready: (p: Page) => Promise<void> }[] = [
  { name: 'office map + correlation', url: '/office?well=DUL-005&r=5&tab=correlation',
    ready: async (p) => {
      await expect(p.getByTestId('office-map')).toBeVisible()
      await expect(p.locator('.js-plotly-plot').first()).toBeVisible({ timeout: 30_000 })
    } },
  { name: 'memory', url: '/office?well=DUL-005&r=5&tab=memory',
    ready: async (p) => { await expect(p.getByTestId('memory-input')).toBeVisible() } },
  { name: 'risk', url: '/office?well=DUL-005&r=5&tab=risk&md=430',
    ready: async (p) => { await expect(p.getByTestId('risk-mode').or(p.getByText(/No precedent|कोई पिछला रिकॉर्ड/))).toBeVisible({ timeout: 30_000 }) } },
  { name: 'review queue', url: '/office?well=DUL-005&r=5&tab=review',
    ready: async (p) => { await expect(p.getByTestId('review-counter-extracted')).toBeVisible({ timeout: 20_000 }) } },
  { name: 'rig', url: '/rig',
    ready: async (p) => { await expect(p.getByTestId('replay-status')).toBeVisible() } },
]

// Dark-theme surface tokens (bg, s1, s2, s3) + the dark Plotly plot area.
const DARK_SURFACES = ['rgb(10, 13, 17)', 'rgb(16, 20, 26)', 'rgb(22, 27, 34)', 'rgb(30, 36, 45)', 'rgb(12, 16, 21)']

/** Every visible element whose background (or SVG fill) is still a dark-theme
 *  surface. The Leaflet map is excluded on purpose: its basemap tiles stay dark
 *  in both themes (tokens.css --map-bg). */
async function darkLeftovers(page: Page) {
  return page.evaluate((dark) => {
    const bad: string[] = []
    for (const el of Array.from(document.querySelectorAll<HTMLElement | SVGElement>('body *'))) {
      if (el.closest('.leaflet-container')) continue
      const cs = getComputedStyle(el)
      if (cs.display === 'none' || cs.visibility === 'hidden') continue
      const bg = cs.backgroundColor.replace(/rgba\((\d+), (\d+), (\d+), 1\)/, 'rgb($1, $2, $3)')
      const fill = el instanceof SVGElement ? cs.fill : ''
      if (dark.includes(bg) || dark.includes(fill)) {
        const cls = typeof el.className === 'string' ? el.className : (el.getAttribute('class') ?? '')
        bad.push(`${el.tagName.toLowerCase()}.${cls.split(' ').slice(0, 3).join('.')} bg=${bg} fill=${fill}`)
      }
    }
    return bad
  }, DARK_SURFACES)
}

test('light theme: first visit follows prefers-color-scheme, no dark surface survives on any screen', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  for (const s of SCREENS) {
    await page.goto(s.url)
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await s.ready(page)
    expect(await darkLeftovers(page), `dark surfaces left on ${s.name}`).toEqual([])
    // body really is the light page token
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(238, 241, 245)')
  }
  // Leaflet controls follow the UI theme even though the basemap stays dark
  await page.goto('/office?well=DUL-005&r=5')
  const zoom = page.locator('.leaflet-control-zoom-in')
  await expect(zoom).toBeVisible()
  expect(await zoom.evaluate((n) => getComputedStyle(n).backgroundColor)).toBe('rgb(255, 255, 255)')
})

test('theme toggle: dark by default, sun/moon button flips and persists across reload', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/office?well=DUL-005&r=5')
  const html = page.locator('html')
  await expect(html).toHaveAttribute('data-theme', 'dark')
  const btn = page.getByTestId('theme-toggle')
  await expect(btn).toHaveAttribute('aria-label', 'Switch to light theme')
  await btn.focus()
  await page.keyboard.press('Enter') // keyboard accessible
  await expect(html).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => localStorage.getItem('nwis.theme'))).toBe('light')
  await page.reload()
  await expect(html).toHaveAttribute('data-theme', 'light') // stored choice beats the OS (dark) scheme
  await page.getByTestId('theme-toggle').click()
  await expect(html).toHaveAttribute('data-theme', 'dark')
})

test('Hindi: EN|हि toggle translates the chrome on every screen, persists, and Devanagari renders', async ({ page }) => {
  await page.goto('/office?well=DUL-005&r=5')
  await page.getByTestId('lang-hi').click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi')
  expect(await page.evaluate(() => localStorage.getItem('nwis.lang'))).toBe('hi')

  await expect(page.getByTestId('nav-office')).toContainText('ऑफ़िस')
  await expect(page.getByTestId('nav-rig')).toContainText('रिग')
  await expect(page.getByTestId('basin-badge')).toContainText('अपर असम')
  await expect(page.getByTestId('tab-correlation')).toContainText('कोरिलेशन')
  await expect(page.getByTestId('tab-review')).toContainText('रिव्यू कतार')
  await expect(page.getByRole('columnheader', { name: 'स्कोर' })).toBeVisible({ timeout: 20_000 })

  await page.goto('/office?well=DUL-005&r=5&tab=memory') // reload: choice persisted
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi')
  await expect(page.getByRole('button', { name: /पूछें/ })).toBeVisible()
  // example questions stay English (the archive is English); the label around them is Hindi
  await expect(page.getByTestId('demo-question').first()).toHaveText('Girujan stuck pipe remedy')
  await expect(page.getByText('आज़माएँ', { exact: true })).toBeVisible()

  await page.goto('/office?well=DUL-005&r=5&tab=risk&md=430')
  const mode = page.getByTestId('risk-mode')
  if (await mode.isVisible({ timeout: 30_000 }).catch(() => false)) {
    await expect(mode).toHaveText(/(संकेतक|प्रशिक्षित|मिश्रित) \((indicator|supervised|mixed)\)/i)
  }

  await page.goto('/office?well=DUL-005&r=5&tab=review')
  await expect(page.getByTestId('review-counter-pending')).toContainText('रिव्यू बाकी', { timeout: 20_000 })

  await page.goto('/rig')
  await expect(page.getByTestId('replay-status')).toContainText(/रीप्ले|रुका|बंद|लॉग/)
  await expect(page.getByText('बिट डेप्थ MD')).toBeVisible()

  // Devanagari has a real font (no tofu): a Devanagari glyph must not measure like .notdef
  const glyphs = await page.evaluate(() => {
    const c = document.createElement('canvas').getContext('2d')!
    c.font = `16px ${getComputedStyle(document.body).fontFamily}`
    return { ka: c.measureText('कि').width, notdef: c.measureText('͸͸').width, family: getComputedStyle(document.body).fontFamily }
  })
  expect(glyphs.family).toContain('Nirmala UI')
  expect(glyphs.ka).toBeGreaterThan(0)
  expect(glyphs.ka).not.toBeCloseTo(glyphs.notdef, 1)

  // Hindi chrome never letter-spaces Devanagari (it breaks the shirorekha) and clipped
  // (overflow:hidden) Hindi text keeps its matras inside the box.
  const problems = await page.evaluate(() => {
    const out: string[] = []
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const own = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent ?? '').join('')
      if (!/[ऀ-ॿ]/.test(own)) continue
      const cs = getComputedStyle(el)
      if (cs.letterSpacing !== 'normal' && parseFloat(cs.letterSpacing) > 0) out.push(`tracked: ${own.slice(0, 30)}`)
      if (cs.overflow === 'hidden' && el.scrollHeight > el.clientHeight + 1) out.push(`clipped: ${own.slice(0, 30)}`)
    }
    return out
  })
  expect(problems).toEqual([])

  // and back to English
  await page.getByTestId('lang-en').click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByTestId('nav-rig')).toContainText('Rig')
})
