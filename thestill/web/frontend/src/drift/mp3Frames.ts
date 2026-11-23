// Spec #116 — MPEG Layer III frame parser, a port of `iter_mp3_frames` in
// thestill/core/sync_track.py. Nothing is decoded: the parser reads frame
// headers and hands out each frame's bytes. It is fed chunks as they arrive
// from the network and keeps only the unparsed tail, so memory stays flat.

const BITRATES_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
const BITRATES_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
const SAMPLE_RATES: Record<number, number[]> = {
  3: [44100, 48000, 32000],
  2: [22050, 24000, 16000],
  0: [11025, 12000, 8000],
}

export interface FrameHeader {
  frameBytes: number
  samples: number
  sampleRate: number
}

/**
 * Parse a Layer III frame header at `buf[i, i+4)`, or null when the four
 * bytes are not one. Free-format streams (bitrate index 0) are not supported.
 */
export function frameHeader(buf: Uint8Array, i: number): FrameHeader | null {
  if (buf[i] !== 0xff || (buf[i + 1] & 0xe0) !== 0xe0) return null
  const version = (buf[i + 1] >> 3) & 0x03
  const layer = (buf[i + 1] >> 1) & 0x03
  if (version === 1 || layer !== 1) return null
  const bitrateIndex = buf[i + 2] >> 4
  const rateIndex = (buf[i + 2] >> 2) & 0x03
  if (bitrateIndex === 0 || bitrateIndex === 15 || rateIndex === 3) return null
  const sampleRate = SAMPLE_RATES[version][rateIndex]
  const padding = (buf[i + 2] >> 1) & 0x01
  if (version === 3) {
    const bitrate = BITRATES_V1_L3[bitrateIndex] * 1000
    return { frameBytes: Math.floor((144 * bitrate) / sampleRate) + padding, samples: 1152, sampleRate }
  }
  const bitrate = BITRATES_V2_L3[bitrateIndex] * 1000
  return { frameBytes: Math.floor((72 * bitrate) / sampleRate) + padding, samples: 576, sampleRate }
}

/**
 * Called for every frame, in order. `buf[offset, offset + length)` holds the
 * frame and is only valid during the call.
 */
export type FrameSink = (startS: number, buf: Uint8Array, offset: number, length: number, header: FrameHeader) => void

/**
 * Streaming frame parser. ID3v2 tags (at the start or between spliced
 * segments) are skipped by their declared size. Anything else that is not a
 * frame is skipped one byte at a time. After any skip, a header is accepted
 * only when the frame it describes is followed by another valid header,
 * which keeps stray 0xFF bytes in tags or artwork from being read as frames.
 */
export class Mp3FrameParser {
  private buf: Uint8Array = new Uint8Array(0)
  private pos = 0
  private timeS = 0
  private inSync = false
  private tagBytesLeft = 0

  constructor(private readonly sink: FrameSink) {}

  /** Feed the next bytes of the stream. */
  push(chunk: Uint8Array): void {
    if (chunk.length === 0) return
    const rest = this.buf.length - this.pos
    if (rest === 0) {
      this.buf = chunk
    } else {
      const joined = new Uint8Array(rest + chunk.length)
      joined.set(this.buf.subarray(this.pos))
      joined.set(chunk, rest)
      this.buf = joined
    }
    this.pos = 0
    this.run(false)
  }

  /** The stream has ended: parse what is left without waiting for more. */
  end(): void {
    this.run(true)
    this.buf = new Uint8Array(0)
    this.pos = 0
  }

  private run(eof: boolean): void {
    const buf = this.buf
    for (;;) {
      const avail = buf.length - this.pos
      if (this.tagBytesLeft > 0) {
        const step = Math.min(this.tagBytesLeft, avail)
        this.pos += step
        this.tagBytesLeft -= step
        if (this.tagBytesLeft > 0) return
        continue
      }
      if (avail < 4) return
      const i = this.pos
      if (buf[i] === 0x49 && buf[i + 1] === 0x44 && buf[i + 2] === 0x33) {
        if (avail >= 10) {
          const size =
            ((buf[i + 6] & 0x7f) << 21) | ((buf[i + 7] & 0x7f) << 14) | ((buf[i + 8] & 0x7f) << 7) | (buf[i + 9] & 0x7f)
          this.tagBytesLeft = 10 + size
          this.inSync = false
          continue
        }
        // A tag header cut off by the end of the stream is not a tag.
        if (!eof) return
      }
      const header = frameHeader(buf, i)
      if (header === null) {
        this.pos += 1
        this.inSync = false
        continue
      }
      const length = header.frameBytes
      if (!this.inSync) {
        // Confirm with the next header before trusting a resync. At the
        // very end of the stream there is no next frame to check.
        if (avail >= length + 4) {
          if (frameHeader(buf, i + length) === null) {
            this.pos += 1
            continue
          }
        } else if (!eof) {
          return
        }
      }
      if (avail < length) return
      this.sink(this.timeS, buf, i, length, header)
      this.timeS += header.samples / header.sampleRate
      this.pos += length
      this.inSync = true
    }
  }
}
