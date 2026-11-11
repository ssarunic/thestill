// Spec #116 §4 step 6 — is the audio element playing the copy that was
// aligned? The map describes the copy the worker fetched. The service
// worker (public/sw.js) reports every response the element receives; two
// checks, both required, tie the two together:
//
// - Length: the element's total byte length equals the fetched length.
// - Content: the first whole 64 KB block of each response matches the block
//   digest of the fetched copy. Equal length alone does not prove equal
//   content.

/** One response the audio element received, as the service worker saw it. */
export interface MediaReport {
  url: string
  /** The request's `Range` header, or null. */
  range: string | null
  status: number
  /** A no-cors response: nothing about it can be read. */
  opaque: boolean
  /** The response's Content-Length, when readable. */
  contentLength: number | null
  /** Index of the first whole block in the response, or null when it holds none. */
  blockIndex: number | null
  crc: number | null
  adler: number | null
  /** `Date.now()` in the worker when the request arrived. */
  t: number
}

export interface FetchedCopy {
  fetchedBytes: number
  /** `[crc, adler, …]` per whole 64 KB block (BlockDigester). */
  blocks: ArrayLike<number>
}

export interface Verification {
  /** True: lengths equal. False: they differ. Null: the element's total is unknown. */
  lengthOk: boolean | null
  rangesChecked: number
  rangesMatched: number
  verdict: 'verified' | 'mismatch' | 'unverified'
}

const RANGE = /^bytes=(\d+)-(\d*)$/

function lengthCheck(reports: readonly MediaReport[], fetchedBytes: number): boolean | null {
  let equal = false
  for (const report of reports) {
    if (report.opaque) continue
    const match = report.range ? RANGE.exec(report.range) : null
    if (!match) {
      // No range: the whole file.
      if (report.status === 200 && report.contentLength !== null) {
        if (report.contentLength !== fetchedBytes) return false
        equal = true
      }
      continue
    }
    if (report.status !== 206) continue
    const start = Number(match[1])
    if (match[2] === '') {
      // Open-ended (Chrome): the response runs to the end of the file, so
      // start + Content-Length is the element's total. Authoritative.
      if (report.contentLength === null) continue
      if (start + report.contentLength !== fetchedBytes) return false
      equal = true
      continue
    }
    // Closed (Safari): proves only that the copy has at least end + 1
    // bytes. Safari's opening `bytes=0-1` says nothing. A range that ends
    // beyond the fetched copy means a longer one; a range that ends exactly
    // on its last byte is how Safari asks for "to the end" of the total it
    // read from Content-Range.
    const end = Number(match[2])
    if (start === 0 && end === 1) continue
    if (end >= fetchedBytes) return false
    if (end === fetchedBytes - 1) equal = true
  }
  return equal ? true : null
}

export function verifyAgainst(reports: readonly MediaReport[], copy: FetchedCopy): Verification {
  const lengthOk = lengthCheck(reports, copy.fetchedBytes)
  let rangesChecked = 0
  let rangesMatched = 0
  for (const report of reports) {
    if (report.opaque || report.blockIndex === null || report.crc === null || report.adler === null) continue
    rangesChecked += 1
    const at = report.blockIndex * 2
    // A block past the end of the fetched copy cannot match.
    if (at + 1 < copy.blocks.length && copy.blocks[at] === report.crc && copy.blocks[at + 1] === report.adler) {
      rangesMatched += 1
    }
  }
  let verdict: Verification['verdict'] = 'unverified'
  if (lengthOk === false || rangesMatched < rangesChecked) verdict = 'mismatch'
  else if (lengthOk === true && rangesChecked > 0) verdict = 'verified'
  return { lengthOk, rangesChecked, rangesMatched, verdict }
}
