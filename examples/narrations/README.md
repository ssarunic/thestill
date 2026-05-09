# Sample narrated digests

Hand-curated example artefacts for spec [#33](../../specs/33-narrated-digest.md).

| File | Purpose |
|------|---------|
| [`example-digest-medium.json`](example-digest-medium.json) | Canonical TTS-ready JSON script (`schema_version: phase2`, `mode: narrated`, six blocks across opener / two segments / tail / signoff). |
| [`example-digest-medium.md`](example-digest-medium.md) | The matching markdown read-through, rendered the way `NarrationView` displays a successful narration. |

These files are committed so contributors and TTS-pipeline developers
can read a real-shaped narration without standing up the full
podcast pipeline. They are referenced from
[`docs/narration.md`](../../docs/narration.md) as the canonical
demo.

The JSON sidecar carries the durable identifier triple
`(episode_id, start_seconds, duration_seconds)` on each quote block
so a downstream TTS consumer can develop against this file before
integrating the live `GET /api/narrations/{id}/script.json` endpoint.
