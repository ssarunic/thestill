# Briefing Audio & Distribution Specification

> **Status:** 📝 Draft
> **Created:** 2026-05-06
> **Updated:** 2026-05-06
> **Author:** Product & Engineering
> **Related:** [#33 narrated-digest](33-narrated-digest.md), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#06 authentication](06-authentication.md), [#07 multi-user-web-app](07-multi-user-web-app.md)

---

## Executive Summary

Turn the briefing script produced by [#33](33-narrated-digest.md) into actual audio and get it to the user wherever they listen — primarily as a **private personal podcast feed** they subscribe to in Apple Podcasts / Overcast / Pocket Casts, plus an in-app player and direct download. Original-audio quote clips are spliced in where the script has quote blocks, so the briefing alternates between a synthesised anchor voice and the real voices of the hosts and guests.

**Mental model:** Each user has their own private podcast — "Sasa's Morning Briefing" — that publishes a new episode every morning. The user subscribes to it once with a token-protected RSS URL; from then on, the daily briefing arrives in their podcast app like any other show. This is the pinnacle thestill is building toward: the user's personal pile of subscriptions, distilled into a single episode that fits in their commute and plays through whichever app they already use.

**Key principle:** **The script is the source of truth.** Everything in this spec consumes [#33](33-narrated-digest.md)'s JSON script and renders it into audio + distribution surfaces. No new editorial choices happen here — only synthesis, splicing, packaging, and delivery. If a briefing reads well, it should listen well; if it doesn't read well, audio won't save it.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Product Requirements](#product-requirements)
3. [Architecture Overview](#architecture-overview)
4. [Audio Generation Pipeline](#audio-generation-pipeline)
5. [TTS Provider Abstraction](#tts-provider-abstraction)
6. [Quote Clip Splicing](#quote-clip-splicing)
7. [Voice Presets](#voice-presets)
8. [Distribution Channels](#distribution-channels)
9. [Personal Podcast Feed](#personal-podcast-feed)
10. [Database & Storage](#database--storage)
11. [API & CLI](#api--cli)
12. [Frontend UX](#frontend-ux)
13. [Security & Privacy](#security--privacy)
14. [Cost & Quotas](#cost--quotas)
15. [Migration Strategy](#migration-strategy)
16. [Out of Scope](#out-of-scope)
17. [Open Questions](#open-questions)
18. [Implementation Phases](#implementation-phases)

---

## Motivation

[Spec #33](33-narrated-digest.md) treats reading as first-class on purpose: the script must work as text. But the *pinnacle* of thestill — the experience the whole pipeline exists to deliver — is the user opening their podcast app on the way to work and hearing one curated episode that distils everything they would otherwise have skipped.

Three reasons to make this an explicit spec rather than a footnote on #33:

1. **Distribution is the hard part, not synthesis.** Calling a TTS API is a few hundred lines. Getting audio into the user's actual listening app — and keeping it private, authenticated, and durable — is where the real design lives. A personal podcast feed solves this elegantly because every podcast app already knows how to consume RSS; we don't build a mobile app.
2. **Quote splicing is what makes it feel real.** TTS reading a quote sounds like TTS reading a quote. The original Lex Fridman line, in his actual voice, dropped into the briefing at the right moment, is what makes this feel like a news show instead of a robot reading a summary. The data path for that — `start_seconds + duration_seconds` already in the JSON — is set up by #33 specifically so this spec can land it.
3. **It changes the product positioning.** Once a user has their morning briefing in their podcast app, thestill stops being "a transcription tool" and becomes "a podcast" — with everything that implies for retention, sharing, and word-of-mouth. Worth getting the seams right the first time.

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| User | Subscribe to my personal briefing in my podcast app (Apple, Overcast, Pocket Casts) | I listen to it the same way I listen to everything else |
| User | Hear the actual hosts' and guests' voices in quote moments, not a robot reading them | The briefing feels like real audio, not synthesised filler |
| User | Pick a voice for the anchor | The briefing matches my preferred listening style |
| User | Have my private feed actually be private | My listening habits aren't browsable by URL guessing |
| User | Revoke and rotate my feed URL | If I ever leak the URL, I can shut it off |
| User | Play the briefing in the web app without leaving | I can listen at my desk without switching apps |
| User | Download the briefing as MP3 | Offline listening works on flights / dead zones |
| User | See in my app the list of recent briefings | I can re-listen to last Tuesday's |
| User | Skip a quote clip if it's running long | Standard podcast scrub controls work |
| User | Have a missing audio fall back gracefully (text view still works) | A TTS outage doesn't kill the briefing |

### Core Behaviors

1. **Audio is rendered from the JSON script** ([#33](33-narrated-digest.md)) with the same content the markdown surface uses. No editorial drift between read and listen modes.
2. **Quote blocks splice original audio** when available; fall back to TTS-of-the-text when the source audio file is missing or out-of-range.
3. **Personal feeds are token-protected** at the URL level (RSS clients don't speak bearer auth). Token can be rotated or revoked from settings.
4. **One canonical MP3 per briefing.** In v1 the audio is shared (one synthesis run per briefing run). When [#29](29-per-user-inbox-fanout.md) brings per-user briefings, audio also becomes per-user.
5. **The text version is still authoritative.** If TTS fails, the feed publishes a text-only entry pointing to the markdown — never an empty episode, never a silent file.
6. **Replays work like any podcast.** Each generated briefing is a feed item with a stable enclosure URL, GUID, and pub_date.
7. **Browser playback uses a streaming-friendly format.** Range requests against the MP3 — same file the feed serves. No transcoding per request.

### Non-Goals

- **Building a mobile app.** Distribution rides on existing podcast clients via RSS.
- **Voice cloning of specific hosts.** Anchor voice is a generic curated voice; we never impersonate.
- **Real-time / live narration.** Batch generation only.
- **Sharing briefings between users.** Each user's feed is private. Cross-user sharing comes via the existing episode-summary view, not the briefing audio.
- **Editing audio after generation.** No in-place fixes; regenerate.
- **Background music / scoring.** v1 is voice + quote clips. Beds and stings are nice-to-have, not required, and each adds licensing complexity.

---

## Architecture Overview

### Layered View

```
┌──────────────────────────────────────────────────────────────────┐
│  Listening Surfaces                                              │
│    • Podcast app (Apple / Overcast / Pocket Casts) ← RSS         │
│    • Web app player                                              │
│    • Direct download (MP3)                                       │
└──────────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────────┐
│  Distribution                                                    │
│    GET  /feeds/personal/<user>/<token>.xml      → RSS 2.0        │
│    GET  /api/briefings/<id>/audio.mp3           → range-served   │
│    GET  /api/briefings                          → list (in-app)  │
│    POST /api/feed-tokens                        → mint / rotate  │
│    DEL  /api/feed-tokens/<id>                   → revoke         │
└──────────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────────┐
│  Services                                                        │
│    BriefingAudioService                                          │
│      .render(narration_id, voice_preset) → BriefingAudio         │
│        1. load JSON script (#33)                                 │
│        2. synthesise narration blocks (TTS)                      │
│        3. extract quote clips from original audio                │
│        4. stitch + normalise + tag                               │
│        5. persist + register in feed                             │
│    PersonalFeedService                                           │
│      .feed_for(user_id) → RSS XML                                │
│      .mint_token(user_id) / .revoke_token(token_id)              │
└──────────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────────┐
│  Providers                                                       │
│    TtsProvider (interface; implementations below)                │
│      ElevenLabsTts │ OpenAiTts │ GoogleCloudTts │ PiperTts (local)│
│    AudioStitcher (pydub / ffmpeg)                                │
└──────────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────────┐
│  Inputs (existing)                                               │
│    data/narrations/<id>.json    (script — produced by #33)       │
│    data/original_audio/...      (source for quote clips)         │
└──────────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────────┐
│  Outputs                                                         │
│    data/briefing_audio/<id>.mp3  (final stitched output)         │
│    data/briefing_audio/<id>.json (audio manifest: chapters,      │
│                                   block→offset map, voice used)  │
└──────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Script ready (data/narrations/<id>.json from #33)
   │
   ▼
BriefingAudioService.render(narration_id, voice_preset)
   │
   ├── For each block in script.blocks:
   │     • narration → TTS → temp wav
   │     • quote     → extract clip from data/original_audio/<podcast>/<episode>.<ext>
   │                   between [start_seconds, start_seconds + duration_seconds]
   │                   → temp wav, leveled to anchor track
   │     • silence between blocks (configurable, ~250ms)
   │
   ├── Stitch all temp wavs into one stream
   │
   ├── Loudness-normalise to -16 LUFS (podcast standard)
   │
   ├── Encode to MP3 (128 kbps, mono — speech-optimised)
   │
   ├── Tag with ID3: title, artist=thestill, album=Briefing,
   │                 chapter markers per segment,
   │                 cover art (generated or stock)
   │
   └── Persist data/briefing_audio/<id>.mp3 + audio manifest
       Register a feed item for each user entitled to this briefing
```

---

## Audio Generation Pipeline

The pipeline is a thin orchestration layer over the TTS provider and pydub. The interesting work is in the manifest: knowing which slice of the final MP3 corresponds to which script block, so the web player can highlight the active block, the chapter markers can drop into podcast clients, and a future "skip this quote" feature has block boundaries to act on.

### Audio Manifest

```json
{
  "narration_id": "2026-05-06-morning",
  "voice_preset": "evergreen",
  "tts_provider": "elevenlabs",
  "tts_voice_id": "...",
  "duration_seconds": 287.4,
  "lufs": -16.1,
  "blocks": [
    {
      "kind": "narration", "section": "opener",
      "start_offset_ms": 0,    "duration_ms": 11800,
      "source": "tts"
    },
    {
      "kind": "quote",     "section": "segment-1",
      "quote_id": "q1", "episode_id": "lenny-zevi-arnovitz",
      "start_offset_ms": 12200, "duration_ms": 11600,
      "source": "original_audio",
      "source_path": "data/original_audio/lennys-podcast/zevi-arnovitz.mp3",
      "source_start_seconds": 59.0,
      "fallback_to_tts": false
    },
    ...
  ],
  "chapters": [
    {"title": "Headline tease",                "start_offset_ms": 0     },
    {"title": "AI coding agents in the wild",  "start_offset_ms": 12000 },
    {"title": "Anthropic enterprise pricing",  "start_offset_ms": 132000},
    {"title": "Also today",                    "start_offset_ms": 252000},
    {"title": "Sign-off",                      "start_offset_ms": 281000}
  ]
}
```

### Stitching & Normalisation

- Inter-block silence: 200–300 ms (configurable). Slightly longer between segments than within.
- Quote clips: 100 ms fade-in, 150 ms fade-out, level-matched to the anchor track within ±2 LU. We do not pitch-shift, time-stretch, or de-noise quote audio in v1 — preserving authenticity matters more than polish.
- Whole-track normalisation to -16 LUFS (standard for spoken-word podcasts, matches Apple's recommendation).
- Encode: MP3 128 kbps mono via ffmpeg (`-c:a libmp3lame -b:a 128k -ac 1 -ar 44100`). Speech doesn't need stereo or higher bitrate. Smaller files are kinder to mobile data plans.

### Cover Art

Auto-generated from a template per briefing — date, lead segment theme, a dimmed thestill mark — at 3000×3000 (Apple's max). Stored alongside the MP3. v1 can ship with a single stock image; per-briefing covers are a polish item.

### Failure Handling

- **TTS fails on a single block** → retry once, then fall back to a synthesised "(audio unavailable for this segment, see text version)" announcement and continue.
- **Quote source audio missing** → mark `fallback_to_tts: true` in the manifest and read the quote text via TTS instead. The block timing is preserved.
- **Whole pipeline fails** → no MP3 is published, but the feed item is still created with a text-only `description`, and the markdown view in the web app remains the primary surface. The feed item flags `<itunes:explicit>` not changed but adds a polite note in the description.

---

## TTS Provider Abstraction

Mirrors the existing LLM provider pattern at [thestill/services/llm_provider.py](../thestill/services/llm_provider.py) (and the transcription-provider pattern at [thestill/core/transcriber_factory.py](../thestill/core/transcriber_factory.py)).

```python
class TtsProvider(Protocol):
    name: str
    def synthesize(self, text: str, voice_id: str, *, format: str = "wav") -> bytes: ...
    def list_voices(self) -> list[Voice]: ...
    def estimate_cost(self, text: str, voice_id: str) -> float: ...
```

### Initial implementations

| Provider | When to pick | Notes |
|---|---|---|
| **ElevenLabs** | Default for hosted | Already wired for STT; same vendor, same key. Best naturalness at speech rates we want. |
| **OpenAI TTS** | Cost-leaning hosted | Cheap, decent voices. |
| **Google Cloud TTS** | Already-on-Google deployments | Wide voice library; long-running async needed for >5k chars. |
| **Piper** (local) | Self-hosted, no API key | Open-source, good quality, fully offline. The "no cloud" path. |

Provider choice is config (`TTS_PROVIDER`); voice selection is a separate config (`TTS_VOICE_PRESET`). Voice presets are an internal abstraction (see [Voice Presets](#voice-presets)) so changing provider doesn't strand existing users on a missing voice.

### Synthesis Granularity

Synthesise each *narration block* as a separate TTS call rather than the whole script in one call. Reasons:

- Fault tolerance: one failed block doesn't kill the run.
- Caching: identical narration blocks across runs (e.g. opener templates) are cache-hit-friendly.
- Better pacing: providers respect block-level pause cues more reliably than mid-paragraph SSML.

Cache key: `sha256(provider + voice_id + text + format)`. Hits skip the API call entirely.

---

## Quote Clip Splicing

The whole point of the audio mode. Each `quote` block in the JSON script carries:

```
episode_id, podcast_title, speaker, start_seconds, duration_seconds
```

`episode_id` resolves to a file under `data/original_audio/<podcast_slug>/<episode_slug>.<ext>` via [PathManager](../thestill/utils/path_manager.py). The clip is extracted with pydub:

```python
clip = AudioSegment.from_file(source_path)[
    int(start_seconds * 1000) : int((start_seconds + duration_seconds) * 1000)
]
clip = clip.fade_in(100).fade_out(150)
clip = match_target_amplitude(clip, anchor_track_dbfs)
```

### Edge cases

- **Source removed since transcription** (storage cleanup): fall back to TTS of the quote text, mark `fallback_to_tts: true` in the manifest.
- **Source has different sample rate / channels** than the anchor track: pydub resamples to a common 44.1 kHz mono before stitching.
- **Quote crosses a chapter break in the source** (rare with our turn-based selection): not a concern; we slice by absolute seconds.
- **Quote is from a YouTube import** (no original audio kept after transcription? — depends on the import policy in [#31](31-import-arbitrary-episodes.md)): if the original is gone, fall back to TTS.

### Storage retention implications

For quote splicing to work, the original audio file must still exist when audio is rendered. Two options:

1. **Keep originals indefinitely** — simplest, costs disk.
2. **Pre-extract quote clips at script-generation time** (in spec #33) and store them as small files alongside the script — decouples script from original. Quote clips are tiny (typically <1 MB each).

Recommendation: **option 2**. Spec #33 should be amended to write `data/narrations/<id>/clips/q1.wav` for each selected quote, so original audio can be cleaned up by retention policy without breaking audio rendering. This is a small follow-up to #33. (Captured as Open Question O3.)

---

## Voice Presets

A preset is a stable internal name that maps to a (provider, voice_id, parameters) tuple per environment. Users pick presets, not raw voice ids.

| Preset | Vibe | Notes |
|---|---|---|
| `evergreen` (default) | News-anchor, calm, slightly warm | Default for new users |
| `dispatch` | Brisk, BBC-World-Service-ish | For users who want it short and snappy |
| `salon` | Conversational, podcast-host | For users who want it to feel like a friend reading |
| `nightly` | Slower, late-evening warmth | For evening briefings |

The preset definitions live at [`thestill/services/voice_presets.yaml`](../thestill/services/voice_presets.yaml) (new file):

```yaml
evergreen:
  description: News-anchor, calm, slightly warm
  providers:
    elevenlabs:
      voice_id: "..."
      stability: 0.55
      similarity_boost: 0.7
      style: 0.15
      speaker_boost: true
    openai:
      voice_id: "alloy"
      speed: 1.0
    piper:
      voice_id: "en_US-amy-medium"
```

Per-user voice preference is a settings field; v1 ships with a single account-wide default and the picker is in the [Frontend UX](#frontend-ux).

---

## Distribution Channels

Three first-class surfaces in v1.

### 1. Personal Podcast Feed (primary)

Token-protected RSS 2.0 + iTunes namespace feed. See [Personal Podcast Feed](#personal-podcast-feed).

### 2. In-App Web Player

Existing player infrastructure ([#22 floating-media-player](22-floating-media-player.md), [#23 transcript-playback-sync](23-transcript-playback-sync.md)) is reused. The briefing reader page gets a play button; clicking it loads the MP3 at `/api/briefings/<id>/audio.mp3` and plays inline. Block highlighting in the markdown view follows the audio cursor using the audio manifest's `start_offset_ms` per block — same pattern as transcript playback sync.

### 3. Direct Download

A `Download MP3` link on the briefing reader page. Same URL as the player, with `Content-Disposition: attachment`.

### Future channels (deferred)

- **Email digest** with audio link (one weekly summary, not daily — daily is what the feed is for).
- **iOS / Android apps** — the feed makes them lower-priority since the user's existing app already works.
- **Smart speakers** (Alexa/Google) via flash briefing skills — interesting but each platform is its own integration project.

---

## Personal Podcast Feed

The killer feature. Each user gets a private RSS feed URL they paste into their podcast app once.

### URL Shape

```
https://<host>/feeds/personal/<user_handle>/<token>.xml
```

- `user_handle` is a human-readable but non-secret identifier (used elsewhere in the app, e.g. profile URLs).
- `token` is a 32-byte URL-safe random secret. **The token alone is what authenticates the request.** The user_handle is in the URL only for human-readable diagnostics — possessing the token without the handle is also accepted.
- The URL is treated as a credential. Logs redact it. The Settings UI shows it once on creation and provides a copy button; thereafter the UI shows a masked form (`...abc123`) and a rotate / revoke action.

### Feed Item Shape

Each generated briefing becomes one `<item>`:

```xml
<item>
  <title>Morning Briefing — May 6, 2026</title>
  <description><![CDATA[
    Today on the briefing: AI coding agents are starting to ship at non-technical
    companies… (truncated, with a "Read full briefing" link to the web app)
  ]]></description>
  <pubDate>Tue, 06 May 2026 07:00:00 +0000</pubDate>
  <guid isPermaLink="false">briefing:2026-05-06-morning:v1</guid>
  <enclosure url="https://.../briefings/2026-05-06-morning/audio.mp3?t=<token>"
             length="4823104" type="audio/mpeg" />
  <itunes:duration>4:47</itunes:duration>
  <itunes:image href="https://.../briefings/2026-05-06-morning/cover.jpg" />
  <itunes:explicit>false</itunes:explicit>
  <itunes:episodeType>full</itunes:episodeType>
</item>
```

### Channel Shape

```xml
<channel>
  <title>Morning Briefing for Sasa</title>
  <description>Your daily distilled briefing across the podcasts you follow.</description>
  <language>en-us</language>
  <itunes:author>thestill</itunes:author>
  <itunes:owner>
    <itunes:name>thestill</itunes:name>
    <itunes:email>you@example.com</itunes:email>
  </itunes:owner>
  <itunes:category text="News"><itunes:category text="Daily News" /></itunes:category>
  <itunes:explicit>false</itunes:explicit>
  <image>...</image>
  ... <item> entries ...
</channel>
```

### Feed Item Retention

Default: last 30 briefings in the feed. Older briefings are still accessible via the in-app history view but drop off the feed to keep client-side syncs fast. Configurable per user (Open Question O4).

### Token Lifecycle

- **Mint** on first opt-in to the feed (button in Settings → Briefing).
- **Rotate** any time. New URL works immediately; old URL returns 410 Gone after a 24h grace window so podcast apps catch up.
- **Revoke** any time. Old URL returns 410 Gone immediately. User mints a new one and re-subscribes.
- **Multiple active tokens** per user are allowed (e.g. a phone token + a desktop token), so revoking one doesn't kill the other.
- **Last-accessed timestamp** is tracked per token for diagnostics ("when did your podcast app last fetch?").

---

## Database & Storage

### New tables

```sql
-- One row per generated briefing audio file
CREATE TABLE briefing_audio (
  id              TEXT PRIMARY KEY,        -- e.g. "2026-05-06-morning"
  narration_id    TEXT NOT NULL,           -- references the JSON script (#33)
  voice_preset    TEXT NOT NULL,
  tts_provider    TEXT NOT NULL,
  duration_ms     INTEGER NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  output_path     TEXT NOT NULL,           -- relative to data/
  manifest_path   TEXT NOT NULL,
  cover_path      TEXT,
  generated_at    TEXT NOT NULL,           -- ISO-8601 +00:00
  fallback_count  INTEGER NOT NULL DEFAULT 0,  -- number of blocks that fell back to TTS
  status          TEXT NOT NULL            -- 'ok' | 'partial' | 'failed'
);
CREATE INDEX idx_briefing_audio_generated_at ON briefing_audio(generated_at);

-- Personal feed tokens
CREATE TABLE feed_tokens (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  token_hash      TEXT NOT NULL UNIQUE,    -- argon2 / bcrypt hash; the plaintext token is shown once
  label           TEXT,                    -- "iPhone", "Desktop", optional user note
  created_at      TEXT NOT NULL,
  last_accessed_at TEXT,
  revoked_at      TEXT,                    -- nullable; non-null = revoked
  rotated_from    TEXT                     -- nullable; previous token id when rotated
);
CREATE INDEX idx_feed_tokens_user_id ON feed_tokens(user_id, revoked_at);

-- Per-user briefing entitlement (which briefings does each user's feed include?)
-- v1: shared briefing across users → entitlement is "every user with feeds enabled"
-- post-#29 per-user briefings → this table becomes per-user authoritative
CREATE TABLE briefing_entitlements (
  briefing_id     TEXT NOT NULL,
  user_id         TEXT NOT NULL,
  PRIMARY KEY (briefing_id, user_id)
);
```

Timestamps follow the project convention from [feedback_sqlite_timestamp_format](../../.claude/projects/-Users-sasasarunic--Sources-thestill/memory/feedback_sqlite_timestamp_format.md): ISO-8601 with `+00:00`, never `CURRENT_TIMESTAMP`.

### File layout

```
data/
├── briefing_audio/
│   ├── 2026-05-06-morning.mp3
│   ├── 2026-05-06-morning.manifest.json
│   ├── 2026-05-06-morning.cover.jpg
│   ├── 2026-05-05-morning.mp3
│   └── ...
└── narrations/
    └── 2026-05-06-morning/
        ├── script.json                  (from #33)
        └── clips/                       (from #33 amended; see Open Question O3)
            ├── q1.wav
            └── q2.wav
```

### Retention policy

- MP3s: keep N days (default 30), then prune. Audio is the largest artefact.
- Manifests + scripts: keep indefinitely (small).
- Quote clips: tied to the script's lifetime.
- Original audio: governed by existing pipeline retention; #33 amended to extract clips so original audio cleanup doesn't break audio rendering.

---

## API & CLI

### CLI

```bash
thestill briefing audio --narration 2026-05-06-morning
thestill briefing audio --narration 2026-05-06-morning --voice salon
thestill briefing audio --dry-run                              # show plan + cost estimate

# Chained from narrate (preferred end-to-end morning workflow)
thestill narrate --audio                                       # script + audio + feed publish
thestill digest --narrate --audio                              # full chain

# Feed admin (single-user / self-hosted)
thestill feed mint                                             # creates a token, prints URL
thestill feed list
thestill feed revoke <token-id>
thestill feed rotate <token-id>
```

### API

```http
POST /api/briefings/{narration_id}/audio
   { "voice_preset": "evergreen" }
→ 202 Accepted (renders async; returns job id)

GET /api/briefings
→ list of generated briefings, paginated

GET /api/briefings/{id}
→ briefing metadata + manifest

GET /api/briefings/{id}/audio.mp3
→ range-served audio (no auth in URL needed in-app; cookie-authed
   for web player; token query-param for feed clients)

GET /feeds/personal/{user_handle}/{token}.xml
→ RSS 2.0 + iTunes feed for the user

POST /api/feed-tokens
   { "label": "iPhone" }
→ 201 with { id, token, url, expires_at }
   (the plaintext token is shown ONCE; only the hash is stored)

GET /api/feed-tokens
→ list of active tokens for the current user (no plaintexts)

POST /api/feed-tokens/{id}/rotate
→ 200 with new token; old token enters 24h grace, then 410

DELETE /api/feed-tokens/{id}
→ 204; old token returns 410 Gone immediately
```

---

## Frontend UX

### Briefing reader (extends #33's reader view)

- **Top bar gains a play button.** Clicking it loads `/api/briefings/<id>/audio.mp3` into the [floating media player](22-floating-media-player.md). Block highlighting in the markdown view follows the audio cursor.
- **Voice picker** in a small dropdown next to the play button — one of the voice presets. Changing voice rerenders the audio (background job; the UI shows "rendering new voice…" until ready).
- **Download MP3** button.
- **Quote blocks** show a small `▶ original audio` indicator when sourced from the original episode (vs TTS'd). Clicking jumps the player to that block.

### Settings → Briefing

A new settings panel.

- **Personal podcast feed**
  - Toggle: enable / disable the personal feed.
  - Active tokens table: label, created, last accessed, rotate, revoke.
  - "+ New token" button. Modal shows the URL once, with copy + "I've saved this" confirm.
  - One-click subscribe links: Apple Podcasts, Overcast, Pocket Casts (these are deep links that take the user's podcast app and pre-fill the feed URL).
- **Voice preset** picker (account default).
- **Briefing length default** picker (links to [#33](33-narrated-digest.md)'s presets).
- **Retention** slider: how many briefings to keep in the feed.

### Onboarding hook

After a user's first successful briefing, the in-app banner offers: "Listen in your podcast app — get the link." One click takes them to the feed-token mint flow.

---

## Security & Privacy

### Token authentication

The feed URL token is the credential. No bearer header is required (RSS clients don't speak it). Treatment:

- Token is 32 bytes from `secrets.token_urlsafe`, ~43 chars.
- Stored as argon2 hash, like a password. Plaintext never persisted.
- Rotation generates a new token and a 24h grace window for the old one (returns 410 Gone after grace).
- Revocation is immediate (no grace).
- Each request bumps `last_accessed_at` for diagnostics.
- Logs redact the token everywhere (URL, Referer, response).
- The audio enclosure URL also accepts the token (`?t=...`) so podcast apps can fetch it; web app uses cookie auth and omits the token.

### Threat model

- **Token leak via screenshot / shared URL** → user rotates from settings.
- **Token leak via server logs** → mitigated by redaction; periodic audit.
- **Brute-force enumeration** → 32 bytes is well past brute-forceable; rate-limit `/feeds/personal/*` to 60 req/min per IP regardless.
- **Malicious feed URL sharing** → an exposed feed can be subscribed by anyone who has the URL. The mitigation is the user revoking the token. We do not bind tokens to IPs or User-Agents because legitimate podcast clients fetch from many IPs (CDN edges, mobile networks). This tradeoff is documented for users in the settings UI.

### Privacy

- The feed URL gives access to the user's full briefing history within the retention window. We don't include any episode data the user couldn't already see in their inbox.
- The briefing audio doesn't include any data marked private (e.g. notes, ratings) — only the public-podcast content the user already follows.
- Per-user briefings (post-#29) are isolated; one user's token never returns another user's briefings.

---

## Cost & Quotas

### Per-briefing TTS cost (estimate)

| Length | Narration words | Chars | ElevenLabs | OpenAI | Piper |
|---|---|---|---|---|---|
| Short (3 min) | ~450 | ~2.7k | ~$0.81 | ~$0.04 | $0 |
| Medium (5 min) | ~750 | ~4.5k | ~$1.35 | ~$0.07 | $0 |
| Long (10 min) | ~1500 | ~9k | ~$2.70 | ~$0.14 | $0 |

(ElevenLabs ≈ $0.30 / 1k chars on default tier; OpenAI ≈ $0.015 / 1k chars; Piper is local.)

Daily medium briefing on ElevenLabs ≈ $40/user/month. **Material at scale.** Two big mitigations:

1. **Block-level cache.** Recurring opener / sign-off / "also today" templates hit cache. Saves ~10–20% per run.
2. **Original-audio quote splicing reduces TTS chars** — every word of a quote played as original audio is a word not synthesised. A quote-heavy 5-minute briefing might be ~3.0k chars instead of 4.5k. Saves ~30% per run.

### Quotas

- **Self-hosted single-user**: no quota; user picks the provider and pays the bill.
- **Hosted multi-user** (eventual): per-user briefing-audio quota — e.g. 1 briefing/day on the free tier, unlimited on paid. Quota is on *generation*, not on serving from cache.
- Voice-rerender (changing voice on a generated briefing) counts against quota.

Quota plumbing follows the import-quota pattern in [#31](31-import-arbitrary-episodes.md).

---

## Migration Strategy

Pure-additive. No backfill required.

1. New tables: `briefing_audio`, `feed_tokens`, `briefing_entitlements`. All wrapped in `IF NOT EXISTS` per existing migration patterns.
2. New service `BriefingAudioService` and `PersonalFeedService`.
3. New CLI commands `thestill briefing audio` and `thestill feed *`.
4. New API endpoints under `/api/briefings/*`, `/api/feed-tokens/*`, `/feeds/personal/*`.
5. Settings panel for feed management.
6. Voice presets shipped as YAML; provider keys configured per-environment.
7. Existing `data/narrations/` artefacts unaffected; audio is opt-in.

If [#29](29-per-user-inbox-fanout.md) lands later: per-user briefings produce per-user audio; entitlements become per-user authoritative; the rest of the pipeline is unchanged.

If [#33](33-narrated-digest.md) is amended to pre-extract quote clips (Open Question O3): that's a small change to the quote-selection step in #33 and a config flag in the audio pipeline to read clips from the script's `clips/` directory instead of original audio. Backwards-compatible.

---

## Out of Scope

- **Voice cloning of named hosts.** Anchor voice is generic.
- **Live / streaming generation.** Batch only.
- **Background music, stings, sound design.** Voice + quote clips only in v1.
- **Smart speaker integrations** (Alexa, Google Assistant flash briefings).
- **Mobile native apps.** RSS feed satisfies the listening case via existing podcast clients.
- **Sharing audio between users.** Each user's feed is private; cross-user sharing happens at the script/summary level, not audio.
- **Editing audio after generation.** Regenerate to change.
- **Per-block manual voice direction** (slow this, emphasise that). The model picks pacing in the script; we render straightforwardly.

---

## Open Questions

| # | Question | Recommendation |
|---|---|---|
| O1 | Default TTS provider? | ElevenLabs for hosted (already wired, best naturalness); Piper for self-hosted no-cloud installs. Configurable. |
| O2 | Single shared briefing audio in v1, or per-user from day one? | Single shared until [#29](29-per-user-inbox-fanout.md) lands. Per-user audio without per-user *content* doesn't pay for itself. |
| O3 | Should #33 pre-extract quote clips into `data/narrations/<id>/clips/`? | Yes — small follow-up to #33. Decouples audio rendering from original-audio retention and avoids redundant decoding on each render. |
| O4 | Feed retention default? | 30 briefings. Configurable per user. |
| O5 | Should audio rendering be sync (block on the request) or async (queue + poll)? | Async via the existing task queue ([#11](11-task-queue-monitor.md), [#20](20-parallel-task-queues.md)). A 5-minute briefing takes 30–90s to render; sync would tie up workers and timeout HTTP. |
| O6 | Cover art per briefing or stock? | Stock for v1; per-briefing covers (date + theme) are a polish item. |
| O7 | Should the feed include a transcript per `<item>` (Apple Podcasts supports this)? | Yes — include the markdown script as the `<podcast:transcript>` element. Free win for accessibility. |
| O8 | Is voice-rerender (re-synth at a different voice) a separate `briefing_audio` row or an in-place overwrite? | Separate row — `(narration_id, voice_preset)` is the natural key. Lets users compare voices and avoids destroying the original render. |
| O9 | Single-user-mode auto-feed token on first run? | Yes for self-hosted single-user (the user is the only user; one token minted on install, displayed in `thestill status`). For multi-user, opt-in only. |
| O10 | What happens if a quote audio is loud or has a sharp cut at the boundary? | The 100ms fade-in / 150ms fade-out + level-match is the v1 mitigation. We don't de-noise or re-EQ — preserving authenticity matters more. If complaints accumulate, revisit. |
| O11 | Should we publish audio to an Apple Podcasts directory listing? | No — the personal feed is private by design. Public discoverability is a separate (and probably never) feature. |

---

## Implementation Phases

### Phase 1 — TTS provider + single-block synthesis

- `TtsProvider` interface + `ElevenLabsTts` implementation (reuse the existing API key plumbing).
- Voice presets YAML + loader.
- `BriefingAudioService.synthesize_block()` for one narration block end-to-end.
- Block-level cache.
- Tests: synthesis, cache hit, voice preset round-trip.

### Phase 2 — Stitching + quote splicing

- Quote clip extraction via pydub.
- Stitching with fades + LUFS normalisation.
- Audio manifest written.
- Fallback paths: missing source audio, TTS failure on a single block.
- ID3 tagging + chapter markers.

### Phase 3 — Personal podcast feed

- `feed_tokens` table + token mint / rotate / revoke service.
- `PersonalFeedService` builds RSS XML.
- `GET /feeds/personal/{handle}/{token}.xml` route with rate limiting + redacted logging.
- `briefing_entitlements` table + entitlement on render.
- Settings UI for token management.
- One-click subscribe links to Apple / Overcast / Pocket Casts.

### Phase 4 — In-app player + voice picker

- Wire the briefing audio into the [floating media player](22-floating-media-player.md).
- Block-cursor highlighting in the markdown view via the audio manifest.
- Voice-preset dropdown on the briefing reader (background re-render).
- Download MP3 button.

### Phase 5 — Polish

- Per-briefing cover art generation.
- Transcript element in the feed (`<podcast:transcript>`).
- Cost dashboard + per-provider metrics.
- `docs/briefing-audio.md` covering provider config, voice presets, feed setup, and troubleshooting.

---

## Cross-References

- **Spec #33** — Narrated digest. Produces the JSON script that this spec consumes. May need a small amendment (pre-extract quote clips; Open Question O3).
- **Spec #29** — Per-user inbox fanout. v1 audio is shared; per-user audio lands once #29 makes content per-user.
- **Spec #06** — Authentication. Personal feed tokens are a parallel credential class to the existing user auth; both should converge on the same user identity.
- **Spec #07** — Multi-user web app. Feed entitlements + per-user voice preferences slot into the multi-user data model.
- **Spec #22** — Floating media player. In-app briefing playback reuses the player.
- **Spec #23** — Transcript playback sync. Block-cursor highlighting follows the same pattern.
- **[transcriber_factory.py](../thestill/core/transcriber_factory.py)** — Provider-abstraction pattern that `TtsProvider` mirrors.
- **[utils/path_manager.py](../thestill/utils/path_manager.py)** — Resolves source audio for quote clip extraction.
