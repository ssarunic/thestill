// Spec #116 §4 — the alignment runs off the main thread so playback and
// scrolling are unaffected. All the logic is in src/drift/run.ts; this
// file only moves messages.
import { runDriftAlignment, type DriftInput, type DriftProgress, type DriftReport } from '../drift/run'

export type DriftWorkerRequest = { type: 'start'; input: DriftInput }
export type DriftWorkerMessage = { type: 'progress'; progress: DriftProgress } | { type: 'done'; report: DriftReport }

const scope = self as unknown as {
  onmessage: ((event: MessageEvent<DriftWorkerRequest>) => void) | null
  postMessage: (message: DriftWorkerMessage) => void
}

scope.onmessage = (event) => {
  if (event.data?.type !== 'start') return
  void runDriftAlignment(event.data.input, {
    onProgress: (progress) => scope.postMessage({ type: 'progress', progress }),
  }).then((report) => scope.postMessage({ type: 'done', report }))
}
