// Spec #116 — is a timeline map expected for an episode's audio?
//
// When the listener starts playback from a transcript time (a click on a
// paragraph), the player wants to know whether a map is on its way: if so
// it stays silent until the map is in force and then starts on the words
// that were clicked, instead of playing the uncorrected position first.
// The reader knows (it holds the transcript and runs the alignment); the
// player asks through this module, as it asks corsVerdict.ts about
// crossorigin, so PlayerContext stays free of drift logic.

const expected = new Set<string>()

/** Said by the reader for the episode it shows, and withdrawn when it is
 * no longer true (no track, a finished attempt without a map, unmount). */
export function setTimelineExpected(episodeId: string, isExpected: boolean): void {
  if (isExpected) expected.add(episodeId)
  else expected.delete(episodeId)
}

export function isTimelineExpected(episodeId: string | null | undefined): boolean {
  return episodeId != null && expected.has(episodeId)
}

/** Test seam. */
export function resetTimelineExpectations(): void {
  expected.clear()
}
