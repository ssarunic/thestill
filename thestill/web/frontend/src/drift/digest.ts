// Spec #116 — the digests of a sync track, as thestill/core/sync_track.py
// computes them. Only CRC-32 and Adler-32, so no crypto library is needed
// and each byte is hashed once. The two implementations are held together
// by tests/fixtures/sync_track/align_vectors.json.

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

/** CRC-32 (zlib) of `bytes[start, end)`, unsigned. */
export function crc32(bytes: Uint8Array, start = 0, end = bytes.length): number {
  let c = 0xffffffff
  for (let i = start; i < end; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

const ADLER_MOD = 65521
// The most bytes that can be summed before `b` could exceed 32 bits.
const ADLER_BLOCK = 5552

/** Adler-32 (zlib) of `bytes[start, end)`, unsigned. */
export function adler32(bytes: Uint8Array, start = 0, end = bytes.length): number {
  let a = 1
  let b = 0
  let i = start
  while (i < end) {
    const stop = Math.min(end, i + ADLER_BLOCK)
    for (; i < stop; i++) {
      a += bytes[i]
      b += a
    }
    a %= ADLER_MOD
    b %= ADLER_MOD
  }
  return ((b << 16) | a) >>> 0
}

/** Frames per landmark (sync_track.WINDOW_FRAMES). */
export const WINDOW_FRAMES = 4

const packed = new Uint8Array(WINDOW_FRAMES * 8)
const packedView = new DataView(packed.buffer)

/**
 * 48-bit digest of four consecutive frame digests, given as
 * `[crc, adler, crc, adler, …]` in frame order. Returned as a number: 48
 * bits fit a double exactly, so it works as a `Map` key.
 */
export function windowDigest(frameDigests: ArrayLike<number>): number {
  for (let i = 0; i < WINDOW_FRAMES * 2; i++) packedView.setUint32(i * 4, frameDigests[i] >>> 0, true)
  return crc32(packed) * 0x10000 + (adler32(packed) & 0xffff)
}

/** Bytes per block of the block digest (spec #116 §4 steps 4 and 6). */
export const BLOCK_BYTES = 65536

/**
 * Digest of every whole `BLOCK_BYTES` block of a byte stream, blocks
 * aligned to the start of the file: CRC-32 and Adler-32 per block, eight
 * bytes for 64 KB. The service worker computes the same pair for the first
 * whole block of each response the audio element receives; equal pairs mean
 * the element is playing the copy that was aligned. A trailing partial
 * block has no entry.
 */
export class BlockDigester {
  private crc = 0xffffffff
  private a = 1
  private b = 0
  private filled = 0
  private readonly pairs: number[] = []

  push(chunk: Uint8Array): void {
    let at = 0
    while (at < chunk.length) {
      const take = Math.min(chunk.length - at, BLOCK_BYTES - this.filled)
      let crc = this.crc
      let a = this.a
      let b = this.b
      let i = at
      const end = at + take
      while (i < end) {
        const stop = Math.min(end, i + ADLER_BLOCK)
        for (; i < stop; i++) {
          const byte = chunk[i]
          crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8)
          a += byte
          b += a
        }
        a %= ADLER_MOD
        b %= ADLER_MOD
      }
      this.crc = crc
      this.a = a
      this.b = b
      this.filled += take
      at = end
      if (this.filled === BLOCK_BYTES) {
        this.pairs.push((this.crc ^ 0xffffffff) >>> 0, ((this.b << 16) | this.a) >>> 0)
        this.crc = 0xffffffff
        this.a = 1
        this.b = 0
        this.filled = 0
      }
    }
  }

  /** `[crc, adler, crc, adler, …]`, one pair per whole block so far. */
  finish(): Uint32Array {
    return Uint32Array.from(this.pairs)
  }
}
