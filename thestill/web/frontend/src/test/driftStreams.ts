// Spec #116 test helpers — the alignment vectors shared with the Python
// aligner, and the synthetic MP3 streams their recipes describe. See
// tests/unit/core/sync_track_vectors.py.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

export interface Part {
  kind: 'frames' | 'silence' | 'id3' | 'junk'
  seed?: number
  count?: number
  skip?: number
  take?: number
  size?: number
}

export interface VectorCase {
  name: string
  track: Part[]
  other: Part[]
  track_stream_bytes: number
  track_stream_crc32: number
  other_stream_bytes: number
  other_stream_crc32: number
  sync_track: string
  expected: {
    anchors_total: number
    anchors_hit: number
    anchors_inlier: number
    other_bytes: number
    other_duration_s: number
    track_duration_s: number
    coverage: number
    spans: { src_start_s: number; src_end_s: number; offset_s: number; anchors: number }[]
  }
}

export interface Vectors {
  digests: { label: string; data: string; crc32: number; adler32: number }[]
  windows: { pairs: [number, number][]; digest: number }[]
  cases: VectorCase[]
}

// Vitest runs from thestill/web/frontend; the fixture lives with the Python tests.
export const vectors: Vectors = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../../tests/fixtures/sync_track/align_vectors.json'), 'utf-8'),
)

export function fromBase64(text: string): Uint8Array {
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0))
}

const frameLength = (index: number) => (index % 49 === 0 ? 418 : 417)

/** Mirror of `xorshift_frames` in sync_track_vectors.py. */
function xorshiftFrames(seed: number, count: number): Uint8Array {
  let total = 0
  for (let i = 0; i < count; i++) total += frameLength(i)
  const out = new Uint8Array(total)
  let state = seed >>> 0
  let at = 0
  for (let index = 0; index < count; index++) {
    const length = frameLength(index)
    out[at] = 0xff
    out[at + 1] = 0xfb
    out[at + 2] = length === 418 ? 0x92 : 0x90
    out[at + 3] = 0x00
    for (let i = 4; i < length; i += 4) {
      state ^= state << 13
      state ^= state >>> 17
      state ^= state << 5
      state >>>= 0
      for (let b = 0; b < 4 && i + b < length; b++) out[at + i + b] = (state >>> (8 * b)) & 0xff
    }
    at += length
  }
  return out
}

function sliceFrames(data: Uint8Array, skip: number, take: number | undefined): Uint8Array {
  const bounds = [0]
  for (let index = 0; bounds[bounds.length - 1] < data.length; index++) {
    bounds.push(bounds[bounds.length - 1] + frameLength(index))
  }
  const end = take === undefined ? bounds.length - 1 : skip + take
  return data.subarray(bounds[skip], bounds[end])
}

export function render(parts: Part[]): Uint8Array {
  const pieces: Uint8Array[] = []
  for (const part of parts) {
    if (part.kind === 'frames') {
      let data = xorshiftFrames(part.seed!, part.count!)
      if (part.skip !== undefined || part.take !== undefined) data = sliceFrames(data, part.skip ?? 0, part.take)
      pieces.push(data)
    } else if (part.kind === 'silence') {
      const frame = new Uint8Array(417)
      frame.set([0xff, 0xfb, 0x90, 0x00])
      for (let i = 0; i < part.count!; i++) pieces.push(frame)
    } else if (part.kind === 'id3') {
      const size = part.size!
      const tag = new Uint8Array(10 + size)
      tag.set([0x49, 0x44, 0x33, 0x04, 0x00, 0x00, (size >> 21) & 0x7f, (size >> 14) & 0x7f, (size >> 7) & 0x7f, size & 0x7f])
      for (let i = 0; i < size; i++) tag[10 + i] = [0xff, 0xfb, 0x90, 0x00][i % 4]
      pieces.push(tag)
    } else {
      pieces.push(new Uint8Array(part.count!))
    }
  }
  const out = new Uint8Array(pieces.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const piece of pieces) {
    out.set(piece, at)
    at += piece.length
  }
  return out
}

export function* chunked(data: Uint8Array, size: number): Generator<Uint8Array> {
  for (let start = 0; start < data.length; start += size) yield data.subarray(start, start + size)
}


export function vectorCase(name: string): VectorCase {
  const found = vectors.cases.find((c) => c.name === name)
  if (!found) throw new Error(`no vector case ${name}`)
  return found
}
