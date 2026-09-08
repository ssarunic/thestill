/**
 * Spec #72 — the Now Playing sheet, end to end and hermetic (every /api/**
 * call is stubbed by the episode fixture). The audio URL is unreachable, so
 * the media element errors out; nothing here depends on audio actually
 * playing — the surface, its close paths, the scrubber, the speed control
 * and the "Open transcript here" deep link are what is under test.
 *
 * Also carries the navigation-contract check for "Open transcript here"
 * (docs/code-guidelines.md "Navigation invariants"): the contract table in
 * navigation-contract.spec.ts cannot express "open a sheet first", so the
 * row lives here.
 */
import { test, expect, type Page } from '@playwright/test'
import { EPISODE_PATH, EPISODE_TITLE, PHONE, mockEpisodeApi } from './episode-hero-fixture'

const RATE_KEY = 'thestill:player:rate'

async function startPlayback(page: Page) {
  await mockEpisodeApi(page)
  await page.goto(EPISODE_PATH)
  await page.getByRole('button', { name: 'Play episode, 58 min' }).click()
  await expect(page.getByRole('region', { name: 'Audio player' })).toBeVisible()
}

async function openSheet(page: Page) {
  await page.getByRole('button', { name: /^Now playing:/ }).click()
  const sheet = page.getByRole('dialog', { name: 'Now playing' })
  await expect(sheet).toBeVisible()
  return sheet
}

test.describe('Now Playing sheet on a phone', () => {
  test.use({ viewport: PHONE, isMobile: true, hasTouch: true })

  test('opens as a modal sheet from the bar, seeks, sets speed, and closes on Esc without touching the bar', async ({ page }) => {
    await startPlayback(page)
    const sheet = await openSheet(page)
    await expect(sheet).toHaveAttribute('aria-modal', 'true')
    await expect(sheet.getByText(EPISODE_TITLE)).toBeVisible()
    await expect(sheet.getByTestId('now-playing-drag-handle')).toBeVisible()
    // Phones have no volume control in the sheet.
    await expect(sheet.getByLabel('Volume')).toHaveCount(0)

    // Duration is known from the episode (58:25), so the scrubber is live
    // even though the stubbed audio never loads (seek plumbing itself is
    // unit-tested; the media element cannot honour a seek without a source).
    const seek = sheet.getByLabel('Seek')
    await expect(seek).toBeEnabled()
    await expect(seek).toHaveAttribute('aria-valuetext', '0:00 of 58:25')
    await expect(sheet.getByText('58:25', { exact: true })).toBeVisible()

    // The speed chip steps 1× → 1.2× on tap and persists.
    await sheet.getByRole('button', { name: 'Speed 1×' }).click()
    await expect(sheet.getByRole('button', { name: 'Speed 1.2×' })).toBeVisible()
    expect(await page.evaluate((k) => localStorage.getItem(k), RATE_KEY)).toBe('1.2')

    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden()
    await expect(page.getByRole('region', { name: 'Audio player' })).toBeVisible()
    await expect(page.getByRole('button', { name: /^Now playing:/ })).toHaveAttribute('aria-expanded', 'false')
  })

  test('Open transcript here lands on the transcript at the current moment, and Back keeps the origin position', async ({ page }) => {
    await startPlayback(page)
    // Scroll the origin page down so Back has a real offset to restore.
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    const before = await page.evaluate(() => window.scrollY)
    expect(before).toBeGreaterThan(100)

    const sheet = await openSheet(page)
    await sheet.getByRole('link', { name: 'Open transcript here' }).click()
    await expect(sheet).toBeHidden()
    await expect(page).toHaveURL(/\?view=transcript&t=\d+$/)
    await expect(page.getByText('Welcome back to Prof G Markets.')).toBeVisible()

    await page.goBack()
    await expect(page).not.toHaveURL(/view=transcript/)
    await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 3000 }).toBeGreaterThan(before - 60)
  })
})

test.describe('Now Playing card on desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('opens as a non-modal card above the bar and closes on a click outside', async ({ page }) => {
    await startPlayback(page)
    const sheet = await openSheet(page)
    await expect(sheet).not.toHaveAttribute('aria-modal', 'true')
    await expect(page.getByTestId('now-playing-drag-handle')).toHaveCount(0)
    await expect(sheet.getByLabel('Volume')).toBeVisible()

    // The card rests above the bar, on the transient rung.
    const card = await sheet.boundingBox()
    const bar = await page.getByRole('region', { name: 'Audio player' }).boundingBox()
    expect(card && bar && card.y + card.height <= bar.y + 1).toBe(true)

    // Speed persists across a reopen.
    await sheet.getByRole('button', { name: 'Speed 1×' }).click()
    await page.mouse.click(640, 100)
    await expect(sheet).toBeHidden()
    await openSheet(page)
    await expect(page.getByRole('button', { name: 'Speed 1.2×' })).toBeVisible()

    // The bar's ✕ clears the session: sheet gone with it.
    await page.getByRole('button', { name: 'Close player' }).click()
    await expect(page.getByRole('dialog', { name: 'Now playing' })).toBeHidden()
    await expect(page.getByRole('region', { name: 'Audio player' })).toHaveCount(0)
  })
})
