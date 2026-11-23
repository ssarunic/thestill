// Spec #116 — reader for the binary sync track written by
// `SyncTrack.to_bytes` in thestill/core/sync_track.py. Little-endian, no
// padding:
//
//   "TSYN" | version u8 | codec u8 | sample_rate u32 | frame_count u32 |
//   audio_bytes u64 | duration_s f64 | generation length u8 | generation |
//   base span count u16 | spans (3 × f64 each) |
//   anchor count u32 | anchors (time_ms u32, digest high u32, digest low u16)

export const ALGORITHM_VERSION = 1
export const CODEC_MP3 = 1

export interface Anchor {
  /** Start of the four-frame window in the tracked copy, in seconds. */
  timeS: number
  /** 48-bit window digest (see digest.ts). */
  digest: number
}

/** Transcript time `[srcStartS, srcEndS)` sits `offsetS` later in the copy
 * the anchors were cut from (Phase 5; empty otherwise). */
export interface BaseSpan {
  srcStartS: number
  srcEndS: number
  offsetS: number
}

export interface SyncTrack {
  version: number
  codec: number
  generation: string
  sampleRate: number
  frameCount: number
  /** Byte length of the transcribed copy. */
  audioBytes: number
  durationS: number
  anchors: Anchor[]
  baseSpans: BaseSpan[]
}

export class SyncTrackError extends Error {}

export function parseSyncTrack(data: ArrayBuffer | Uint8Array): SyncTrack {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data)
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  try {
    if (bytes.length < 4 || bytes[0] !== 0x54 || bytes[1] !== 0x53 || bytes[2] !== 0x59 || bytes[3] !== 0x4e) {
      throw new SyncTrackError('not a sync track')
    }
    let pos = 4
    const version = view.getUint8(pos)
    const codec = view.getUint8(pos + 1)
    if (version !== ALGORITHM_VERSION) throw new SyncTrackError(`unsupported sync track version ${version}`)
    const sampleRate = view.getUint32(pos + 2, true)
    const frameCount = view.getUint32(pos + 6, true)
    // A u64, but a file length is far below 2^53.
    const audioBytes = view.getUint32(pos + 10, true) + view.getUint32(pos + 14, true) * 0x100000000
    const durationS = view.getFloat64(pos + 18, true)
    const generationLength = view.getUint8(pos + 26)
    pos += 27
    if (pos + generationLength > bytes.length) throw new RangeError('generation')
    const generation = new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(pos, pos + generationLength))
    pos += generationLength

    const spanCount = view.getUint16(pos, true)
    pos += 2
    const baseSpans: BaseSpan[] = []
    for (let i = 0; i < spanCount; i++) {
      baseSpans.push({
        srcStartS: view.getFloat64(pos, true),
        srcEndS: view.getFloat64(pos + 8, true),
        offsetS: view.getFloat64(pos + 16, true),
      })
      pos += 24
    }

    const anchorCount = view.getUint32(pos, true)
    pos += 4
    if (pos + anchorCount * 10 > bytes.length) throw new RangeError('anchors')
    const anchors: Anchor[] = new Array(anchorCount)
    for (let i = 0; i < anchorCount; i++) {
      anchors[i] = {
        timeS: view.getUint32(pos, true) / 1000,
        digest: view.getUint32(pos + 4, true) * 0x10000 + view.getUint16(pos + 8, true),
      }
      pos += 10
    }
    return { version, codec, generation, sampleRate, frameCount, audioBytes, durationS, anchors, baseSpans }
  } catch (error) {
    if (error instanceof SyncTrackError) throw error
    // DataView throws RangeError past the end; TextDecoder throws TypeError.
    throw new SyncTrackError('truncated or corrupt sync track')
  }
}
