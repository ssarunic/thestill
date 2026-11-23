/**
 * The key entities strip and the entity rail list the same entities, so
 * only one is shown at a time: the strip below `lg` (1024 px), where the
 * rail is hidden, and the rail from `lg` up. The type toggles live in
 * whichever is on screen.
 */
import { test, expect, type Page } from '@playwright/test'
import { EPISODE_PATH, mockEpisodeApi } from './episode-hero-fixture'

function entity(i: number, type: 'person' | 'company' | 'product' | 'topic') {
  return {
    entity: { id: `${type}-${i}`, type, canonical_name: `${type} number ${i}`, wikidata_qid: null },
    mention_count: 3,
    first_mention_ms: i * 1000,
    speaker_kind: 'unknown',
    salience: 1 - i / 100,
    mentions: [],
  }
}

async function open(page: Page, width: number) {
  await page.setViewportSize({ width, height: 900 })
  await mockEpisodeApi(page)
  await page.route('**/api/episodes/*/entities*', (route) =>
    route.fulfill({
      json: { status: 'ok', entities: [entity(1, 'person'), entity(2, 'company'), entity(3, 'product'), entity(4, 'topic')] },
    }),
  )
  await page.goto(EPISODE_PATH)
  await expect(page.getByText('company number 2').first()).toBeAttached()
}

for (const width of [393, 820]) {
  test(`below lg (${width} px): the strip lists the entities and holds the type toggles; no rail`, async ({ page }) => {
    await open(page, width)
    const strip = page.getByTestId('key-entities-strip')
    await expect(strip).toBeVisible()
    await expect(strip.getByText('company number 2')).toBeVisible()
    await expect(strip.getByRole('button', { name: 'Hide companys' })).toBeVisible()
    await expect(page.getByTestId('entity-rail')).toBeHidden()
  })
}

test('from lg up (1280 px): the rail lists the entities and holds the type toggles; no strip', async ({ page }) => {
  await open(page, 1280)
  await expect(page.getByTestId('key-entities-strip')).toBeHidden()
  const rail = page.getByTestId('entity-rail')
  await expect(rail).toBeVisible()
  await expect(rail.getByText('company number 2')).toBeVisible()
  // Each entity is on screen once, not twice.
  await expect(page.getByText('company number 2').filter({ visible: true })).toHaveCount(1)

  const toggle = rail.getByRole('button', { name: 'Hide companys' })
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  await toggle.click()
  await expect(rail.getByRole('button', { name: 'Show companys' })).toHaveAttribute('aria-pressed', 'false')
  // The rail is the index: the company is still listed.
  await expect(rail.getByText('company number 2')).toBeVisible()
})
