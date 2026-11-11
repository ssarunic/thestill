// Spec #116 — public/sw.js run in a stand-in worker scope. It is plain
// JavaScript with no build step, so the test loads the file as shipped.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BLOCK_BYTES, BlockDigester } from './digest'
import { verifyAgainst, type MediaReport } from './verify'

const SOURCE = readFileSync(resolve(process.cwd(), 'public/sw.js'), 'utf-8')

function fileBytes(length: number): Uint8Array {
  const out = new Uint8Array(length)
  let state = 7
  for (let i = 0; i < length; i++) {
    state = (Math.imul(state, 1103515245) + 12345) >>> 0
    out[i] = state >>> 24
  }
  return out
}

const FILE = fileBytes(BLOCK_BYTES * 4 + 999)

function streamOf(bytes: Uint8Array, chunkSize: number): ReadableStream<Uint8Array> {
  let at = 0
  return new ReadableStream({
    pull(controller) {
      if (at >= bytes.length) return controller.close()
      controller.enqueue(bytes.slice(at, at + chunkSize))
      at += chunkSize
    },
  })
}

type Listener = (event: never) => void

function loadWorker(fetchImpl: (request: Request) => Promise<Response>) {
  const listeners: Record<string, Listener> = {}
  const posted: { kind: string; report: MediaReport }[] = []
  const client = { postMessage: (message: { kind: string; report: MediaReport }) => posted.push(message) }
  const scope = {
    location: { origin: 'https://thestill.me' },
    addEventListener: (name: string, listener: Listener) => (listeners[name] = listener),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(), get: async () => client, matchAll: async () => [client] },
  }
  new Function('self', 'fetch', SOURCE)(scope, fetchImpl)

  /** Dispatch a fetch event; resolves with the response once the worker has finished its report. */
  async function request(url: string, init: { destination: string; range?: string; mode?: string }) {
    const headers = new Headers(init.range ? { range: init.range } : {})
    // An element loaded with `crossorigin` makes CORS requests.
    const req = { url, destination: init.destination, mode: init.mode ?? 'cors', headers } as unknown as Request
    let responded: Promise<Response> | null = null
    const pending: Promise<unknown>[] = []
    listeners.fetch({
      request: req,
      clientId: 'c1',
      respondWith: (r: Promise<Response>) => (responded = r),
      waitUntil: (p: Promise<unknown>) => pending.push(p),
    } as never)
    if (!responded) return null
    const response = await (responded as Promise<Response>)
    await Promise.all(pending)
    return response
  }
  return { request, posted, scope, listeners }
}

/** A host that honours Range like a CDN. */
function rangeServer(file: Uint8Array, chunkSize = 30_000) {
  const served: Response[] = []
  const fetchImpl = async (request: Request) => {
    const range = request.headers.get('range')
    const match = range ? /^bytes=(\d+)-(\d*)$/.exec(range) : null
    const start = match ? Number(match[1]) : 0
    const end = match && match[2] !== '' ? Number(match[2]) : file.length - 1
    const body = file.subarray(start, end + 1)
    const response = new Response(streamOf(body, chunkSize), {
      status: match ? 206 : 200,
      headers: { 'content-length': String(body.length) },
    })
    served.push(response)
    return response
  }
  return { fetchImpl, served }
}

function digestOf(file: Uint8Array) {
  const digester = new BlockDigester()
  digester.push(file)
  return { fetchedBytes: file.length, blocks: digester.finish() }
}

let server: ReturnType<typeof rangeServer>
beforeEach(() => {
  server = rangeServer(FILE)
})

describe('service worker', () => {
  it('leaves everything but cross-origin audio and video to the browser', async () => {
    const worker = loadWorker(server.fetchImpl)
    expect(await worker.request('https://cdn.example/app.js', { destination: 'script' })).toBeNull()
    expect(await worker.request('https://cdn.example/a.json', { destination: '' })).toBeNull()
    expect(await worker.request('https://cdn.example/cover.jpg', { destination: 'image' })).toBeNull()
    expect(await worker.request('https://thestill.me/api/narration.mp3', { destination: 'audio' })).toBeNull()
    expect(await worker.request('https://thestill.me/', { destination: 'document' })).toBeNull()
    expect(worker.posted).toEqual([])
    expect(server.served).toEqual([])
  })

  it('leaves a request made without CORS to the browser', async () => {
    // An element without `crossorigin`: every host not yet known to allow
    // reads, and every host that never will (the BBC's redirects to plain
    // http, which the browser upgrades for the element but blocks for a
    // worker). Relaying these broke playback in production.
    const worker = loadWorker(server.fetchImpl)
    for (const destination of ['audio', 'video']) {
      expect(
        await worker.request('http://open.live.bbc.co.uk/x.mp3', { destination, range: 'bytes=0-', mode: 'no-cors' }),
      ).toBeNull()
    }
    expect(worker.posted).toEqual([])
    expect(server.served).toEqual([])
  })

  it('hands the element the original response, untouched', async () => {
    const worker = loadWorker(server.fetchImpl)
    const response = await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=0-' })
    expect(response).toBe(server.served[0])
    expect(new Uint8Array(await response!.arrayBuffer())).toEqual(FILE)
  })

  it('reports block pairs that the page’s digest of the same file confirms', async () => {
    const worker = loadWorker(server.fetchImpl)
    const last = FILE.length - 1
    // Chrome's opening request, then Safari's three shapes, then a mid-block seek.
    await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=0-' })
    await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=0-1' })
    await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: `bytes=0-${last}` })
    await worker.request('https://cdn.example/a.mp3', { destination: 'video', range: `bytes=70000-${last}` })

    const reports = worker.posted.map((m) => m.report)
    expect(worker.posted.every((m) => m.kind === 'drift-media')).toBe(true)
    expect(reports.map((r) => r.blockIndex)).toEqual([0, null, 0, 2])
    expect(reports.map((r) => r.contentLength)).toEqual([FILE.length, 2, FILE.length, FILE.length - 70000])
    expect(reports.map((r) => r.status)).toEqual([206, 206, 206, 206])
    expect(verifyAgainst(reports, digestOf(FILE))).toEqual({
      lengthOk: true,
      rangesChecked: 3,
      rangesMatched: 3,
      verdict: 'verified',
    })
  })

  it('gives pairs that expose a different copy of the same length', async () => {
    const other = FILE.slice()
    other[BLOCK_BYTES * 2 + 5] ^= 0xff
    const worker = loadWorker(rangeServer(other).fetchImpl)
    await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=0-' })
    await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=131072-' })
    const result = verifyAgainst(
      worker.posted.map((m) => m.report),
      digestOf(FILE),
    )
    expect(result).toMatchObject({ rangesChecked: 2, rangesMatched: 1, verdict: 'mismatch' })
  })

  it('reports an unreadable response without trying to read it', async () => {
    const opaque = { type: 'opaque', status: 0, headers: new Headers(), body: null } as unknown as Response
    const worker = loadWorker(async () => opaque)
    const response = await worker.request('https://nocors.example/a.mp3', { destination: 'audio', range: 'bytes=0-' })
    expect(response).toBe(opaque)
    expect(worker.posted[0].report).toMatchObject({ opaque: true, blockIndex: null, contentLength: null })
  })

  it('lets a failed fetch fail as it would without a worker', async () => {
    const worker = loadWorker(async () => {
      throw new TypeError('Load failed')
    })
    await expect(
      worker.request('https://down.example/a.mp3', { destination: 'audio', range: 'bytes=0-' }),
    ).rejects.toThrow('Load failed')
    expect(worker.posted).toEqual([])
  })

  it('takes control at once and answers a ping', () => {
    const worker = loadWorker(server.fetchImpl)
    worker.listeners.install({} as never)
    expect(worker.scope.skipWaiting).toHaveBeenCalled()
    const waited: unknown[] = []
    worker.listeners.activate({ waitUntil: (p: unknown) => waited.push(p) } as never)
    expect(worker.scope.clients.claim).toHaveBeenCalled()
    const source = { postMessage: vi.fn() }
    worker.listeners.message({ data: { kind: 'drift-ping' }, source } as never)
    expect(source.postMessage).toHaveBeenCalledWith({ kind: 'drift-pong' })
  })

  it('stands down when the page says the session may no longer align', async () => {
    const worker = loadWorker(server.fetchImpl)
    expect(await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=0-' })).not.toBeNull()
    worker.listeners.message({ data: { kind: 'drift-disable' }, source: null } as never)
    // From here the browser handles media requests itself, as with no worker.
    expect(await worker.request('https://cdn.example/a.mp3', { destination: 'audio', range: 'bytes=0-' })).toBeNull()
    expect(server.served).toHaveLength(1)
    expect(worker.posted).toHaveLength(1)
  })
})
