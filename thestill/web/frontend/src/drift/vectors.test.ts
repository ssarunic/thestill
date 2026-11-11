// Spec #116 — the browser aligner against the vectors the Python aligner
// produced (tests/unit/core/sync_track_vectors.py). Both sides rebuild the
// same synthetic MP3 streams from the recipes in the fixture, so a
// difference in parsing, digests or fitting shows up as a failed case.
import { describe, expect, it } from 'vitest'
import { chunked, fromBase64, render, vectors } from '../test/driftStreams'
import { Aligner, align } from './align'
import { adler32, crc32, windowDigest } from './digest'
import { Mp3FrameParser } from './mp3Frames'
import { parseSyncTrack, SyncTrackError } from './syncTrack'

describe('digests', () => {
  it.each(vectors.digests)('crc32 and adler32 of $label match zlib', ({ data, crc32: crc, adler32: adler }) => {
    const bytes = fromBase64(data)
    expect(crc32(bytes)).toBe(crc)
    expect(adler32(bytes)).toBe(adler)
  })

  it('hashes a sub-range without copying', () => {
    const bytes = fromBase64(vectors.digests.find((d) => d.label === 'abc')!.data)
    const padded = new Uint8Array([9, ...bytes, 9])
    expect(crc32(padded, 1, 4)).toBe(crc32(bytes))
    expect(adler32(padded, 1, 4)).toBe(adler32(bytes))
  })

  it.each(vectors.windows)('window digest matches Python', ({ pairs, digest }) => {
    expect(windowDigest(pairs.flat())).toBe(digest)
  })
})

describe('sync track reader', () => {
  it('reads the header and anchors Python wrote', () => {
    const first = vectors.cases[0]
    const track = parseSyncTrack(fromBase64(first.sync_track))
    expect(track.generation).toBe('vectors')
    expect(track.sampleRate).toBe(44100)
    expect(track.audioBytes).toBe(first.track_stream_bytes)
    expect(track.durationS).toBe(first.expected.track_duration_s)
    expect(track.anchors).toHaveLength(first.expected.anchors_total)
    expect(track.baseSpans).toEqual([])
  })

  it('rejects bytes that are not a track, and a truncated one', () => {
    const good = fromBase64(vectors.cases[0].sync_track)
    expect(() => parseSyncTrack(new Uint8Array([1, 2, 3, 4, 5]))).toThrow(SyncTrackError)
    expect(() => parseSyncTrack(good.subarray(0, good.length - 3))).toThrow(SyncTrackError)
    expect(() => parseSyncTrack(good.subarray(0, 20))).toThrow(SyncTrackError)
    const wrongVersion = good.slice()
    wrongVersion[4] = 99
    expect(() => parseSyncTrack(wrongVersion)).toThrow(/version/)
  })
})

describe('aligner', () => {
  it.each(vectors.cases)('$name', (testCase) => {
    const other = render(testCase.other)
    // The recipe rebuilt here is byte for byte what Python aligned.
    expect(other.length).toBe(testCase.other_stream_bytes)
    expect(crc32(other)).toBe(testCase.other_stream_crc32)

    const track = parseSyncTrack(fromBase64(testCase.sync_track))
    const result = align(track, chunked(other, 4099))
    const expected = testCase.expected

    expect(result.anchorsTotal).toBe(expected.anchors_total)
    expect(result.anchorsHit).toBe(expected.anchors_hit)
    expect(result.anchorsInlier).toBe(expected.anchors_inlier)
    expect(result.otherBytes).toBe(expected.other_bytes)
    expect(result.otherDurationS).toBe(expected.other_duration_s)
    expect(result.coverage).toBe(expected.coverage)
    expect(result.spans).toEqual(
      expected.spans.map((span) => ({
        srcStartS: span.src_start_s,
        srcEndS: span.src_end_s,
        offsetS: span.offset_s,
        anchors: span.anchors,
      })),
    )
  })

  it('gives the same answer whatever the chunk size', () => {
    const testCase = vectors.cases.find((c) => c.name === 'tags, junk and silence between segments')!
    const other = render(testCase.other)
    const track = parseSyncTrack(fromBase64(testCase.sync_track))
    const whole = align(track, [other])
    for (const size of [1, 7, 417, 65536]) {
      // One-byte chunks are slow over a whole stream; a prefix is enough to
      // cross every kind of boundary (tag, junk, frame, resync).
      const data = size === 1 ? other.subarray(0, 120_000) : other
      const reference = size === 1 ? align(track, [data]) : whole
      expect(align(track, chunked(data, size))).toEqual(reference)
    }
  })

  it('reports frames as they are parsed, for progress', () => {
    const testCase = vectors.cases[0]
    const aligner = new Aligner(parseSyncTrack(fromBase64(testCase.sync_track)))
    const other = render(testCase.other)
    aligner.push(other.subarray(0, other.length >> 1))
    const halfway = aligner.frameCount
    expect(halfway).toBeGreaterThan(0)
    aligner.push(other.subarray(other.length >> 1))
    aligner.finish()
    expect(aligner.frameCount).toBeGreaterThan(halfway)
  })
})

describe('frame parser', () => {
  it('parses the track stream to the frame count Python recorded', () => {
    for (const testCase of vectors.cases) {
      const stream = render(testCase.track)
      expect(crc32(stream)).toBe(testCase.track_stream_crc32)
      let frames = 0
      let endS = 0
      const parser = new Mp3FrameParser((startS, _buf, _offset, _length, header) => {
        frames += 1
        endS = startS + header.samples / header.sampleRate
      })
      for (const chunk of chunked(stream, 7919)) parser.push(chunk)
      parser.end()
      const track = parseSyncTrack(fromBase64(testCase.sync_track))
      expect(frames).toBe(track.frameCount)
      expect(endS).toBe(track.durationS)
    }
  })
})
