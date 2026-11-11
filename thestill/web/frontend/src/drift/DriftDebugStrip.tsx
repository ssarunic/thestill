// Spec #116 §7.6 — the admin-only `?driftdebug=1` strip: what the aligner
// made of this browser's copy of the audio, drawn on the transcript's
// timeline, with a way to check each span by ear.
import type { PlayCheck } from './report'
import type { DriftAlignmentState } from './useDriftAlignment'

function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

const megabytes = (bytes: number | null) => (bytes === null ? '—' : `${(bytes / 1048576).toFixed(1)} MB`)
const signed = (seconds: number) => `${seconds >= 0 ? '+' : '−'}${Math.abs(seconds).toFixed(3)} s`

const OUTCOME_TEXT: Record<string, string> = {
  identity: 'Same audio as the transcribed copy',
  aligned: 'Aligned',
  variant_mismatch: 'Aligned, but the audio playing is a different copy',
  low_confidence: 'Too little of the transcript was found',
  no_cors: 'The audio host does not allow the page to read the file',
  unsupported_codec: 'Not an MP3 the aligner can read',
  error: 'Failed',
}

const SW_TEXT: Record<string, string> = {
  unsupported: 'this browser has no service worker here',
  not_controlling: 'the service worker was not yet in control of this page; reload and play again',
  element_not_cors:
    'the audio loaded without crossorigin, as it does the first time a host is seen; play the episode again after a reload',
  no_requests: 'the service worker saw no request from the audio element',
  opaque: 'the service worker could not read the audio responses',
}

/** One line on whether the element was shown to be playing the aligned copy. */
function checkText(check: PlayCheck | null): string {
  if (!check) return 'Check against the playing audio: pending.'
  const v = check.verification
  if (check.swState !== 'ok' || !v) {
    return `Check against the playing audio: not possible — ${SW_TEXT[check.swState] ?? check.swState}.`
  }
  const length = v.lengthOk === null ? 'length unknown' : v.lengthOk ? 'same length' : 'DIFFERENT length'
  const ranges = `${v.rangesMatched} of ${v.rangesChecked} ranges match`
  const verdict =
    v.verdict === 'verified'
      ? 'the element is playing the aligned copy'
      : v.verdict === 'mismatch'
        ? 'the element is playing a DIFFERENT copy; this map does not apply'
        : 'not enough to decide'
  return `Check against the playing audio: ${verdict} (${length}, ${ranges}).`
}

export default function DriftDebugStrip({
  state,
  hasTrack,
  onPlayAt,
}: {
  state: DriftAlignmentState
  /** Whether the transcript names a sync track at all. */
  hasTrack: boolean
  /** Seek the player to a time on the listener's copy and play. */
  onPlayAt: (seconds: number) => void
}) {
  const report = state.report
  const spans = report?.spans ?? []
  const gaps = report?.gaps ?? []
  const endS = Math.max(1, ...spans.map((s) => s.srcEndS), ...gaps.map((g) => g.srcEndS))
  const percent = (seconds: number) => `${(100 * seconds) / endS}%`

  let headline: string
  if (!hasTrack) headline = 'No sync track for this transcript'
  else if (state.status === 'idle') headline = 'Waiting for playback of this episode’s audio to start'
  else if (state.status === 'running') {
    const p = state.progress
    headline = p
      ? `Reading the audio: ${megabytes(p.fetchedBytes)}${p.totalBytes ? ` of ${megabytes(p.totalBytes)}` : ''}`
      : 'Starting'
  } else if (!report) headline = 'The alignment worker failed to start'
  else headline = OUTCOME_TEXT[report.outcome] ?? report.outcome

  return (
    <div
      data-testid="drift-debug-strip"
      className="mb-4 rounded-md border border-slate-300 bg-slate-50 px-4 py-3 text-xs text-slate-800"
    >
      <p className="font-medium">
        Drift alignment (admin) — {headline}
        {report?.error ? ` (${report.error})` : ''}
      </p>
      <p className="mt-1 text-slate-600">Reported only. Seeks and highlighting are not corrected yet.</p>
      {state.status === 'done' && report && <p className="mt-1 text-slate-600">{checkText(state.check)}</p>}

      {report && (
        <>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
            <div>
              <dt className="text-slate-500">Host</dt>
              <dd className="break-all">{report.audioHost ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Read / transcribed copy</dt>
              <dd>
                {megabytes(report.fetchedBytes)} / {megabytes(report.audioBytes)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Landmarks found</dt>
              <dd>
                {report.anchorsHit ?? '—'} of {report.anchorsTotal ?? '—'}
                {report.anchorsInlier !== null && report.anchorsHit !== null
                  ? ` (${report.anchorsHit - report.anchorsInlier} outside a span)`
                  : ''}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Coverage</dt>
              <dd>{report.coverage === null ? '—' : `${(report.coverage * 100).toFixed(1)}%`}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Fetch / fit</dt>
              <dd>
                {report.fetchMs === null ? '—' : `${(report.fetchMs / 1000).toFixed(1)} s`} /{' '}
                {report.fitMs === null ? '—' : `${report.fitMs} ms`}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Absent from this copy</dt>
              <dd>{report.gapsConfirmedS === null ? '—' : `${report.gapsConfirmedS.toFixed(1)} s`}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Unresolved</dt>
              <dd>{report.unresolvedS === null ? '—' : `${report.unresolvedS.toFixed(1)} s`}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Ads in this copy</dt>
              <dd>{report.listenerAdsS === null ? '—' : `${report.listenerAdsS.toFixed(1)} s`}</dd>
            </div>
          </dl>

          {(spans.length > 0 || gaps.length > 0) && (
            <div
              className="relative mt-3 h-3 w-full overflow-hidden rounded bg-white ring-1 ring-slate-300"
              role="img"
              aria-label="Transcript timeline: matched spans, absent stretches and unresolved stretches"
            >
              {spans.map((span, i) => (
                <div
                  key={`s${i}`}
                  className="absolute inset-y-0 bg-emerald-500"
                  style={{ left: percent(span.srcStartS), width: percent(span.srcEndS - span.srcStartS) }}
                  title={`${clock(span.srcStartS)}–${clock(span.srcEndS)} plays ${signed(span.offsetS)} later`}
                />
              ))}
              {gaps.map((gap, i) => (
                <div
                  key={`g${i}`}
                  className={`absolute inset-y-0 ${gap.kind === 'confirmed' ? 'bg-slate-400' : 'bg-amber-400'}`}
                  style={{ left: percent(gap.srcStartS), width: percent(gap.srcEndS - gap.srcStartS) }}
                  title={`${clock(gap.srcStartS)}–${clock(gap.srcEndS)} ${
                    gap.kind === 'confirmed' ? 'absent from this copy' : 'unresolved'
                  }`}
                />
              ))}
            </div>
          )}
          {(spans.length > 0 || gaps.length > 0) && (
            <p className="mt-1 text-slate-500">
              <span className="mr-3">
                <span className="mr-1 inline-block h-2 w-2 rounded-sm bg-emerald-500" aria-hidden="true" />
                matched
              </span>
              <span className="mr-3">
                <span className="mr-1 inline-block h-2 w-2 rounded-sm bg-slate-400" aria-hidden="true" />
                absent from this copy
              </span>
              <span>
                <span className="mr-1 inline-block h-2 w-2 rounded-sm bg-amber-400" aria-hidden="true" />
                unresolved
              </span>
            </p>
          )}

          {spans.length > 0 && (
            <table className="mt-3 w-full text-left">
              <thead className="text-slate-500">
                <tr>
                  <th className="pr-3 font-normal">Transcript time</th>
                  <th className="pr-3 font-normal">Offset in this copy</th>
                  <th className="font-normal">Check by ear</th>
                </tr>
              </thead>
              <tbody>
                {spans.map((span, i) => (
                  <tr key={i} className="border-t border-slate-200">
                    <td className="py-1 pr-3 tabular-nums">
                      {clock(span.srcStartS)}–{clock(span.srcEndS)}
                    </td>
                    <td className="py-1 pr-3 tabular-nums">{signed(span.offsetS)}</td>
                    <td className="py-1">
                      <button
                        type="button"
                        className="mr-3 underline"
                        onClick={() => onPlayAt(span.srcStartS + span.offsetS)}
                      >
                        Play span start, corrected
                      </button>
                      <button type="button" className="underline" onClick={() => onPlayAt(span.srcStartS)}>
                        Play uncorrected
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
