/**
 * Perceptual lead applied to playback-position highlights — the karaoke
 * read-word colouring and the active-segment highlight.
 *
 * The visual cue fires this many media-seconds *before* the acoustic
 * onset. Without it every latency in the chain stacks on the late side:
 * the eye needs ~100–200 ms to saccade to a newly-highlighted word, the
 * rAF read → React commit → paint pipeline adds ~16–33 ms, and
 * Whisper-family word start timestamps land tens of milliseconds after
 * the true onset. Karaoke UIs (Apple Music Sing, Musixmatch) lead for
 * the same reason. Tuned by ear in the 0.10–0.20 s range; past ~0.25 s
 * the highlight reads as "jumping ahead".
 *
 * Deliberately NOT applied to `activeIdx` / `aria-current` — for
 * assistive tech "the word actually being spoken now" is the honest
 * answer.
 */
export const HIGHLIGHT_LEAD_SECONDS = 0.15
