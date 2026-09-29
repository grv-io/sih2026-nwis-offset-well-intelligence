import { expect, test, type Page } from '@playwright/test'

// The judge walkthrough (docs/DEMO_SCRIPT.md) against the static build: every panel
// must render from the recorded responses, with zero console errors.

function watchConsole(page: Page) {
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`${m.text()} (${m.location().url})`)
  })
  page.on('pageerror', (e) => errors.push(String(e)))
  return errors
}

test('static demo: office, memory, risk, review, rig, language, theme, Volve', async ({ page }) => {
  const errors = watchConsole(page)

  await test.step('deep link: office map for DUL-005 at 5 km ranks 6 offsets', async () => {
    await page.goto('office?well=DUL-005&r=5')
    await expect(page.getByTestId('office-page')).toBeVisible()
    await expect(page.getByTestId('static-badge')).toBeVisible()
    await expect(page.getByTestId('office-map')).toBeVisible()
    await expect(page.getByTestId('offset-table').locator('tbody tr')).toHaveCount(6, { timeout: 20_000 })
    await expect(page.getByTestId('offset-table')).toContainText('DUL-012')
  })

  await test.step('radius change re-ranks offsets', async () => {
    await page.getByTestId('radius-slider').fill('3')
    await expect(page).toHaveURL(/r=3/)
    await expect(async () => {
      const n = await page.getByTestId('offset-table').locator('tbody tr').count()
      expect(n).toBeGreaterThan(0)
      expect(n).toBeLessThan(6)
    }).toPass({ timeout: 10_000 })
    await page.getByTestId('radius-slider').fill('5')
    await expect(page.getByTestId('offset-table').locator('tbody tr')).toHaveCount(6)
  })

  await test.step('switch to another well', async () => {
    await page.getByTestId('well-picker').click()
    await page.getByTestId('well-search').fill('MOR-00')
    await page.getByRole('option', { name: /MOR-003/ }).click()
    await expect(page).toHaveURL(/well=MOR-003/)
    await expect(page.getByTestId('offset-table')).toBeVisible()
    await expect(page.locator('.js-plotly-plot').first()).toBeVisible({ timeout: 30_000 })
    await page.goto('office?well=DUL-005&r=5')
  })

  await test.step('correlation in both depth modes + dip readout', async () => {
    await expect(page.locator('.js-plotly-plot').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('dip-readout')).toContainText('0.12')
    await page.getByRole('radio', { name: 'True vertical depth' }).click()
    await expect(page.locator('.js-plotly-plot').first()).toBeVisible()
    await page.getByRole('radio', { name: 'Formation-aligned' }).click()
    await expect(page.locator('.js-plotly-plot').first()).toBeVisible()
  })

  await test.step('memory: recorded answer with citations opens the source page', async () => {
    await page.getByTestId('tab-memory').click()
    await page.getByTestId('demo-question').filter({ hasText: 'Girujan stuck pipe remedy' }).click()
    const card = page.getByTestId('answer-card')
    await expect(card).toBeVisible()
    await expect(card.getByTestId('citation-chip').first()).toBeVisible()
    await card.getByTestId('citation-chip').first().click()
    await expect(page.getByTestId('document-text')).toBeVisible()
    const footer = (await page.locator('[role="dialog"] footer').innerText()).trim()
    expect(footer).toMatch(/^[\w.-]+\.(pdf|txt)$/)
    await page.keyboard.press('Escape')
  })

  const ask = async (q: string) => {
    await page.getByTestId('memory-input').fill(q)
    await page.getByRole('button', { name: 'Ask' }).click()
  }

  await test.step('scope guard: greeting and off-topic', async () => {
    await ask('hello')
    await expect(page.getByTestId('answer-card')).toContainText('Hello. I am the NWIS archive assistant')
    await ask('what is the weather in Jaipur')
    await expect(page.getByTestId('answer-card')).toContainText('I only answer questions about the drilling archive')
  })

  await test.step('unrecorded archive question: honest static message + examples', async () => {
    await ask('Kopili torque spike while reaming')
    await expect(page.getByTestId('answer-card')).toContainText('This static demo answers the listed example questions')
    await expect(page.getByTestId('answer-suggestions')).toBeVisible()
  })

  await test.step('risk ahead of bit at 430 m', async () => {
    await page.goto('office?well=DUL-005&r=5&tab=risk&md=430')
    await expect(page.getByTestId('risk-intervals')).toContainText('500–550 m', { timeout: 20_000 })
    await expect(page.getByTestId('risk-mode')).toBeVisible()
  })

  await test.step('review queue: approve one row', async () => {
    await page.getByTestId('tab-review').click()
    await expect(page.getByTestId('review-counter-extracted')).toBeVisible()
    await page.getByRole('radio', { name: 'All extracted' }).click()
    await expect(page.getByTestId('review-table')).toBeVisible()
    const before = await page.getByTestId('review-counter-reviewed').innerText()
    await page.getByTestId('review-table').getByRole('button', { name: /Approve/ }).first().click()
    await expect(page.getByTestId('review-table')).toContainText(/Approved/i)
    await expect(page.getByTestId('review-counter-reviewed')).not.toHaveText(before)
  })

  await test.step('ground-truth toggle', async () => {
    await page.getByRole('switch').first().click()
    await expect(page.getByTestId('truth-banner')).toBeVisible()
    await page.getByRole('switch').first().click()
    await expect(page.getByTestId('truth-banner')).toHaveCount(0)
  })

  await test.step('rig replay: DUL-005 from 430 m at 500x -> advisory -> recommendation -> investigate -> ack', async () => {
    await page.getByTestId('nav-rig').click()
    await expect(page.getByTestId('rig-page')).toBeVisible()
    await expect(page.getByTestId('rig-static-note')).toBeVisible()
    await page.getByLabel('Start at measured depth').fill('430')
    await page.getByRole('radio', { name: '500×' }).click()
    await page.getByTestId('start-replay').click()
    await expect(page.getByTestId('replay-status')).toHaveAttribute('data-status', 'rig.status.live', { timeout: 15_000 })
    const card = page.getByTestId('alert-card').filter({ hasText: 'Lookahead advisory' }).first()
    await expect(card).toBeVisible({ timeout: 20_000 })
    await expect(card).toContainText('Recommendation pending')
    await expect(card).not.toContainText('Recommendation pending', { timeout: 15_000 })
    await card.getByTestId('investigate-button').click()
    await expect(page.getByTestId('document-text').first()).toBeVisible({ timeout: 10_000 })
    await page.keyboard.press('Escape')
    await card.getByTestId('ack-button').click()
    await expect(page.getByTestId('alert-card').filter({ hasText: 'Acknowledged' }).first()).toBeVisible()
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(page.getByTestId('replay-status')).toHaveAttribute('data-status', 'rig.status.paused')
    await page.getByRole('button', { name: 'Resume' }).click()
    await page.getByLabel('Seek to measured depth').fill('1500')
    await page.getByRole('button', { name: 'Seek', exact: true }).click()
    await expect(page.getByTestId('bit-depth')).toContainText(/1,[45]\d\d/)
    await page.getByRole('button', { name: 'Stop' }).click()
    await expect(page.getByTestId('start-replay')).toBeVisible()
  })

  await test.step('English <-> Hindi', async () => {
    await page.getByTestId('lang-hi').click()
    await expect(page.getByTestId('nav-office')).toContainText('ऑफ़िस')
    await expect(page.getByTestId('static-badge')).toContainText('स्टैटिक डेमो')
    await page.getByTestId('lang-en').click()
    await expect(page.getByTestId('nav-office')).toContainText('Office')
  })

  await test.step('light / dark theme', async () => {
    const html = page.locator('html')
    const before = (await html.getAttribute('data-theme')) ?? 'dark'
    await page.getByTestId('theme-toggle').click()
    await expect(html).not.toHaveAttribute('data-theme', before)
    await page.getByTestId('theme-toggle').click()
    await expect(html).toHaveAttribute('data-theme', before)
  })

  await test.step('Volve basin', async () => {
    await page.getByTestId('nav-office').click()
    await page.getByRole('radio', { name: /Volve/ }).click()
    await expect(page.getByTestId('basin-badge')).toContainText('VOLVE')
    await expect(page.getByTestId('office-map')).toBeVisible()
    await expect(page.getByTestId('memory-basin-note')).toBeVisible()
  })

  expect(errors, errors.join('\n')).toEqual([])
})
