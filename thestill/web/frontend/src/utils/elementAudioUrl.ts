// The address the media element loads for an episode's audio.
//
// The reader is an https page, and a browser will not load http media into
// one. Most browsers retry the address over https at every redirect hop;
// that fails where a host answers one browser differently (the BBC's
// download host sends Safari to an http-only file for the last hop, so the
// audio never loads there). An http enclosure is therefore played through
// our own endpoint, which follows the redirects over https and answers with
// the file's address (GET …/episodes/{slug}/audio, a 302). The listener
// still downloads the audio from the publisher's host.
//
// Only where it is needed: on an https page (on http, local development,
// the enclosure loads as it is), and in Safari-family browsers. Chrome and
// Firefox reach the audio through the browser's own retry, and are left to
// fetch it straight from the publisher as before.

export interface AudioOwner {
  podcastSlug: string
  episodeSlug: string
  audioUrl: string
}

// Safari, and every browser on an iPhone or iPad (all WebKit underneath):
// WebKit that does not also call itself Chrome, Edge or Android.
const WEBKIT = /AppleWebKit\//
const NOT_SAFARI_FAMILY = /\b(?:Chrome|Chromium|Edg|Android)\b/

export function isSafariFamily(userAgent: string): boolean {
  return WEBKIT.test(userAgent) && !NOT_SAFARI_FAMILY.test(userAgent)
}

export function elementAudioUrl(
  url: string,
  owner: AudioOwner | null,
  pageProtocol: string = typeof window === 'undefined' ? 'http:' : window.location.protocol,
  userAgent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent,
): string {
  if (pageProtocol !== 'https:' || !url.startsWith('http://') || !isSafariFamily(userAgent)) return url
  // Only the episode's own enclosure: the endpoint knows no other asset.
  if (!owner || owner.audioUrl !== url || !owner.podcastSlug || !owner.episodeSlug) return url
  return `/api/podcasts/${encodeURIComponent(owner.podcastSlug)}/episodes/${encodeURIComponent(owner.episodeSlug)}/audio`
}
