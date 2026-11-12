/**
 * What a press on Play looks like while the audio is on its way, and when
 * it never arrives: the button shows a spinner at once and stays pressable,
 * and a source that fails is reported in the mini player instead of leaving
 * the listener with a button that did nothing.
 */
import { test, expect } from '@playwright/test'
import { EPISODE_PATH, mockEpisodeApi } from './episode-hero-fixture'

const AUDIO = 'https://audio.example.test/episode.mp3'

test('the play button shows the wait, and a source that fails is said out loud', async ({ page }) => {
  await mockEpisodeApi(page, { audioUrl: AUDIO })
  // A host that takes its time and then refuses.
  await page.route(AUDIO, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500))
    await route.abort('connectionrefused')
  })
  await page.goto(EPISODE_PATH)

  const hero = page.getByRole('button', { name: /^Play episode/ })
  await expect(hero).not.toHaveAttribute('aria-busy', 'true')
  await hero.click()

  // At once: both play buttons show the wait, and neither is disabled.
  const miniToggle = page.getByRole('region', { name: 'Audio player' }).getByRole('button', { name: /^(Play|Pause)$/ })
  await expect(miniToggle).toHaveAttribute('aria-busy', 'true')
  await expect(miniToggle).toBeEnabled()
  await expect(page.locator('main button[aria-busy="true"]').first()).toBeVisible()
  await expect(page.getByTestId('mini-player-error')).toHaveCount(0)

  // The source fails: said in the bar, and the buttons are Play again.
  await expect(page.getByTestId('mini-player-error')).toHaveText("Couldn't load the audio. Try again in a moment.")
  await expect(miniToggle).not.toHaveAttribute('aria-busy', 'true')
  await expect(page.getByRole('region', { name: 'Audio player' }).getByRole('button', { name: 'Play', exact: true })).toBeVisible()
})

test('a second press while waiting cancels the wait', async ({ page }) => {
  await mockEpisodeApi(page, { audioUrl: AUDIO })
  await page.route(AUDIO, () => {
    /* never answers */
  })
  await page.goto(EPISODE_PATH)
  await page.getByRole('button', { name: /^Play episode/ }).click()

  const bar = page.getByRole('region', { name: 'Audio player' })
  const toggle = bar.getByRole('button', { name: /^(Play|Pause)$/ })
  await expect(toggle).toHaveAttribute('aria-busy', 'true')
  await toggle.click()
  await expect(bar.getByRole('button', { name: 'Play', exact: true })).not.toHaveAttribute('aria-busy', 'true')
})
