import { describe, expect, it } from 'vitest'
import { adler32, BLOCK_BYTES, BlockDigester, crc32 } from './digest'
import { verifyAgainst, type MediaReport } from './verify'

function bytes(length: number, seed = 1): Uint8Array {
  const out = new Uint8Array(length)
  let state = seed
  for (let i = 0; i < length; i++) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0
    out[i] = state >>> 24
  }
  return out
}

function digest(data: Uint8Array, chunk = 50_001): Uint32Array {
  const digester = new BlockDigester()
  for (let i = 0; i < data.length; i += chunk) digester.push(data.subarray(i, i + chunk))
  return digester.finish()
}

const FILE = bytes(BLOCK_BYTES * 5 + 1234)
const COPY = { fetchedBytes: FILE.length, blocks: digest(FILE) }

/** What sw.js reports for a response starting at `start`. */
function seen(range: string | null, start: number, length: number, from: Uint8Array = FILE): MediaReport {
  const blockIndex = Math.ceil(start / BLOCK_BYTES)
  const at = blockIndex * BLOCK_BYTES
  const whole = at + BLOCK_BYTES <= start + length && at + BLOCK_BYTES <= from.length
  const block = from.subarray(at, at + BLOCK_BYTES)
  return {
    url: 'https://cdn.example/a.mp3',
    range,
    status: range ? 206 : 200,
    opaque: false,
    contentLength: length,
    blockIndex: whole ? blockIndex : null,
    crc: whole ? crc32(block) : null,
    adler: whole ? adler32(block) : null,
    t: 0,
  }
}

describe('BlockDigester', () => {
  it('gives one CRC-32 and Adler-32 pair per whole block, whatever the chunking', () => {
    expect(COPY.blocks).toHaveLength(10)
    for (let block = 0; block < 5; block++) {
      const slice = FILE.subarray(block * BLOCK_BYTES, (block + 1) * BLOCK_BYTES)
      expect(COPY.blocks[block * 2]).toBe(crc32(slice))
      expect(COPY.blocks[block * 2 + 1]).toBe(adler32(slice))
    }
    expect(digest(FILE, 1 << 20)).toEqual(COPY.blocks)
    expect(digest(FILE, 7)).toEqual(COPY.blocks)
    expect(digest(FILE.subarray(0, BLOCK_BYTES - 1))).toHaveLength(0)
  })
})

describe('verifyAgainst', () => {
  it('verifies a Chrome play: an open-ended request and a later seek', () => {
    const total = FILE.length
    const result = verifyAgainst(
      [seen('bytes=0-', 0, total), seen('bytes=200000-', 200000, total - 200000)],
      COPY,
    )
    expect(result).toEqual({ lengthOk: true, rangesChecked: 2, rangesMatched: 2, verdict: 'verified' })
  })

  it('verifies a Safari play: the two-byte probe says nothing, the closed ranges do', () => {
    const last = FILE.length - 1
    const result = verifyAgainst(
      [
        seen('bytes=0-1', 0, 2),
        seen(`bytes=0-${last}`, 0, FILE.length),
        seen(`bytes=131072-${last}`, 131072, FILE.length - 131072),
      ],
      COPY,
    )
    expect(result).toEqual({ lengthOk: true, rangesChecked: 2, rangesMatched: 2, verdict: 'verified' })
  })

  it('does not take Safari’s opening bytes=0-1 for a two-byte file', () => {
    const result = verifyAgainst([seen('bytes=0-1', 0, 2)], COPY)
    expect(result).toEqual({ lengthOk: null, rangesChecked: 0, rangesMatched: 0, verdict: 'unverified' })
  })

  it('needs a range that reaches the last byte before calling a closed-range length equal', () => {
    const result = verifyAgainst([seen('bytes=0-99999', 0, 100000)], COPY)
    expect(result.lengthOk).toBeNull()
    expect(result.verdict).toBe('unverified')
    expect(result.rangesMatched).toBe(1)
  })

  it('finds a longer copy by its length, open-ended or closed', () => {
    const longer = FILE.length + 40_000
    expect(verifyAgainst([seen('bytes=0-', 0, longer)], COPY).verdict).toBe('mismatch')
    expect(verifyAgainst([seen(`bytes=0-${longer - 1}`, 0, longer)], COPY)).toMatchObject({
      lengthOk: false,
      verdict: 'mismatch',
    })
  })

  it('finds a copy of the same length with different audio', () => {
    // Two ad breaks of other lengths that sum to the same total.
    const other = FILE.slice()
    other.set(bytes(BLOCK_BYTES, 99), BLOCK_BYTES * 2)
    const result = verifyAgainst(
      [seen('bytes=0-', 0, FILE.length, other), seen('bytes=131072-', 131072, FILE.length - 131072, other)],
      COPY,
    )
    expect(result).toEqual({ lengthOk: true, rangesChecked: 2, rangesMatched: 1, verdict: 'mismatch' })
  })

  it('checks a range that starts mid-block from the next boundary', () => {
    const report = seen('bytes=70000-', 70000, FILE.length - 70000)
    expect(report.blockIndex).toBe(2)
    expect(verifyAgainst([report], COPY).rangesMatched).toBe(1)
  })

  it('cannot verify what it cannot read', () => {
    const opaque: MediaReport = { ...seen('bytes=0-', 0, FILE.length), opaque: true, status: 0 }
    expect(verifyAgainst([opaque], COPY).verdict).toBe('unverified')
    expect(verifyAgainst([], COPY).verdict).toBe('unverified')
  })

  it('treats a block beyond the fetched copy as a mismatch', () => {
    const report = { ...seen('bytes=0-', 0, FILE.length), blockIndex: 40 }
    expect(verifyAgainst([report], COPY).verdict).toBe('mismatch')
  })
})
