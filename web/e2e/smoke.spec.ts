import { expect, test } from '@playwright/test'

test('/ redirects to /office and the office workspace loads', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/office/)
  await expect(page.getByTestId('office-page')).toBeVisible()
  await expect(page.getByTestId('basin-badge')).toContainText('ILLUSTRATIVE SYNTHETIC DATA')
  await expect(page.getByTestId('office-map')).toBeVisible()
  await expect(page.getByTestId('offset-table')).toBeVisible({ timeout: 20_000 })
})

test('well picker searches and switches the active well', async ({ page }) => {
  await page.goto('/office?well=DUL-005')
  await page.getByTestId('well-picker').click()
  await page.getByTestId('well-search').fill('MOR-00')
  await page.getByRole('option', { name: /MOR-003/ }).click()
  await expect(page).toHaveURL(/well=MOR-003/)
  await expect(page.getByTestId('well-picker')).toContainText('MOR-003')
})

test('office tabs render without crashing', async ({ page }) => {
  await page.goto('/office?well=DUL-005&r=5')
  for (const tab of ['memory', 'risk', 'review', 'correlation']) {
    await page.getByTestId(`tab-${tab}`).click()
    await expect(page.getByTestId(`tab-${tab}`)).toHaveAttribute('aria-selected', 'true')
  }
  await expect(page.getByTestId('dip-readout')).toContainText('dip', { timeout: 20_000 })
})

test('/rig loads the alert-first view', async ({ page }) => {
  await page.goto('/rig')
  await expect(page.getByTestId('rig-page')).toBeVisible()
  await expect(page.getByTestId('replay-status')).toBeVisible()
  await expect(page.getByTestId('bit-depth')).toBeVisible()
})
