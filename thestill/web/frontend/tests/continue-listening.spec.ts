/**
 * Spec #90 — continue listening, end to end and hermetic: play, reload, and
 * the bar comes back on the same episode at (about) the same position. The
 * fixture's audio URL is served as a generated silent WAV so the media
 * element really plays and seeks.
 */
import { test, expect, type Page } from '@playwright/test'
import { EPISODE_PATH, EPISODE_TITLE, mockEpisodeApi } from './episode-hero-fixture'

// 60 s of 8 kHz 8-bit mono silence (~480 KB).
function silentWav(seconds: number, rate = 8000): Buffer {
  const samples = seconds * rate
  const buffer = Buffer.alloc(44 + samples)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + samples, 4)
  buffer.write('WAVE', 8)
  buffer.write('fmt ', 12)
  buffer.writeUInt32LE(16, 16) // PCM chunk size
  buffer.writeUInt16LE(1, 20) // PCM
  buffer.writeUInt16LE(1, 22) // mono
  buffer.writeUInt32LE(rate, 24)
  buffer.writeUInt32LE(rate, 28) // byte rate
  buffer.writeUInt16LE(1, 32) // block align
  buffer.writeUInt16LE(8, 34) // bits per sample
  buffer.write('data', 36)
  buffer.writeUInt32LE(samples, 40)
  buffer.fill(128, 44) // 8-bit silence is the midpoint
  return buffer
}

// Served with byte ranges, like a podcast CDN. Without them the media is not
// seekable until fully buffered, so the restore's seek at loadedmetadata was
// usually dropped: the bar came back at 0:00 and the test passed only when
// playback climbed back past the mark inside the poll window.
async function servePlayableAudio(page: Page) {
  const wav = silentWav(60)
  await page.route('https://example.com/a.mp3', (route) => {
    const range = /bytes=(\d+)-(\d*)/.exec(route.request().headers()['range'] ?? '')
    if (!range) {
      return route.fulfill({ status: 200, contentType: 'audio/wav', headers: { 'Accept-Ranges': 'bytes' }, body: wav })
    }
    const start = Number(range[1])
    const end = range[2] ? Number(range[2]) : wav.length - 1
    return route.fulfill({
      status: 206,
      contentType: 'audio/wav',
      headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` },
      body: wav.subarray(start, end + 1),
    })
  })
}

const seekValue = async (page: Page) =>
  Number(await page.getByRole('region', { name: 'Audio player' }).getByRole('slider', { name: 'Seek' }).inputValue())

test('a reload brings the bar back on the same episode near the same position', async ({ page }) => {
  await mockEpisodeApi(page)
  await servePlayableAudio(page)
  await page.goto(EPISODE_PATH)
  await page.getByRole('button', { name: 'Play episode, 58 min' }).click()
  await expect(page.getByRole('region', { name: 'Audio player' })).toBeVisible()
  await expect.poll(() => seekValue(page), { timeout: 10_000 }).toBeGreaterThan(3)
  const before = await seekValue(page)

  await page.reload()

  const bar = page.getByRole('region', { name: 'Audio player' })
  await expect(bar.getByRole('button', { name: `Now playing: ${EPISODE_TITLE}` })).toBeVisible({ timeout: 2_000 })
  await expect.poll(() => seekValue(page), { timeout: 5_000 }).toBeGreaterThanOrEqual(before - 2)
  // Autoplay after a reload is best-effort; either way the position is not
  // far past where the page went away.
  expect(await seekValue(page)).toBeLessThan(before + 8)
})
