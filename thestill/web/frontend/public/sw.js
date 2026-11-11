// Spec #116 §4 step 6 — thestill's service worker. Its whole job is to tell
// the page what the audio element is being served, so the reader can check
// that a timestamp map was built from the same copy.
//
// Rules (specs/116-timestamp-drift-alignment.md, Phase 2):
// - It answers only cross-origin requests whose destination is audio or
//   video, and returns the ORIGINAL response. A response rebuilt from a
//   stream loses Content-Range and the browser rejects it.
// - Every other request is left to the browser. Nothing is cached and no
//   navigation is handled.
// - It is registered only for sessions that may align (drift.align). A
//   session that may not tells it to stand down, unregisters it and reloads
//   once. This file is served with no-cache.
//
// Plain JavaScript, no build step: the URL and the bytes stay stable.

const BLOCK_BYTES = 65536
const ADLER_MOD = 65521

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

// CRC-32 and Adler-32 of one block: the same pair src/drift/digest.ts
// (BlockDigester) computes for the copy the page fetched.
function blockPair(bytes) {
  let crc = 0xffffffff
  let a = 1
  let b = 0
  let i = 0
  while (i < bytes.length) {
    const stop = Math.min(bytes.length, i + 5552)
    for (; i < stop; i++) {
      const byte = bytes[i]
      crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8)
      a += byte
      b += a
    }
    a %= ADLER_MOD
    b %= ADLER_MOD
  }
  return [(crc ^ 0xffffffff) >>> 0, ((b << 16) | a) >>> 0]
}

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

// Set by the page when its session may no longer align. Unregistering does
// not detach a worker from a page it already controls, so the page tells it
// to stand down and then reloads once to get out from under it.
let disabled = false

self.addEventListener('fetch', (event) => {
  if (disabled) return
  const request = event.request
  if (request.destination !== 'audio' && request.destination !== 'video') return
  if (new URL(request.url).origin === self.location.origin) return
  event.respondWith(relay(event))
})

async function tell(clientId, report) {
  const client = clientId ? await self.clients.get(clientId) : null
  const targets = client ? [client] : await self.clients.matchAll({ type: 'window' })
  for (const target of targets) target.postMessage({ kind: 'drift-media', report })
}

// Read the first whole block of the file that this response contains.
async function firstBlock(response, start, report) {
  const blockIndex = Math.ceil(start / BLOCK_BYTES)
  const skip = blockIndex * BLOCK_BYTES - start
  const need = skip + BLOCK_BYTES
  const reader = response.body.getReader()
  const block = new Uint8Array(BLOCK_BYTES)
  let seen = 0
  try {
    while (seen < need) {
      const { done, value } = await reader.read()
      if (done) break
      // Copy the part of this chunk that falls inside [skip, need).
      const from = Math.max(0, skip - seen)
      const to = Math.min(value.length, need - seen)
      if (to > from) block.set(value.subarray(from, to), seen + from - skip)
      seen += value.length
    }
  } finally {
    reader.cancel().catch(() => {})
  }
  if (seen >= need) {
    const [crc, adler] = blockPair(block)
    report.blockIndex = blockIndex
    report.crc = crc
    report.adler = adler
  }
}

async function relay(event) {
  const request = event.request
  const range = request.headers.get('range')
  const report = {
    url: request.url,
    range,
    status: 0,
    opaque: false,
    contentLength: null,
    blockIndex: null,
    crc: null,
    adler: null,
    t: Date.now(),
  }
  const response = await fetch(request)
  report.status = response.status
  report.opaque = response.type === 'opaque'
  const length = Number(response.headers.get('content-length'))
  if (!report.opaque && Number.isFinite(length) && length > 0) report.contentLength = length

  const match = range ? /^bytes=(\d+)-/.exec(range) : null
  const readable = !report.opaque && response.body && (response.status === 200 || response.status === 206)
  // A 206 without a parsable range, or a 200 answering a range request,
  // gives no trustworthy offset: report it without a block.
  const start = response.status === 206 ? (match ? Number(match[1]) : null) : 0
  if (readable && start !== null) {
    event.waitUntil(
      firstBlock(response.clone(), start, report)
        .catch(() => {})
        .then(() => tell(event.clientId, report)),
    )
  } else {
    event.waitUntil(tell(event.clientId, report))
  }
  return response
}

self.addEventListener('message', (event) => {
  if (event.data && event.data.kind === 'drift-disable') {
    disabled = true
    return
  }
  if (event.data && event.data.kind === 'drift-ping' && event.source) {
    event.source.postMessage({ kind: 'drift-pong' })
  }
})
