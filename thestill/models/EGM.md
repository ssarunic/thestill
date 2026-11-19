# thestill.models

## Overview

Pydantic (and two dataclass) data models shared by the repositories,
services, web API, MCP server, and CLI. The package performs no I/O: models
validate shape, derive state, and default ids and timestamps. Persistence
lives in `thestill/repositories/`, and replaying edits lives in
`thestill/core/transcript_edits.py`.

`__init__.py` exports nothing (`__all__ = []`), so import every model by its
module path, for example `from thestill.models.podcast import Episode`.

## Interface

### Podcasts and episodes (`podcast.py`)

- `podcast.py:EpisodeState`: str enum with the values `discovered`,
  `downloaded`, `downsampled`, `transcribed`, `cleaned`, `summarized`, and
  `failed`.
- `podcast.py:FailureType`: `transient` (retries exhausted) and `fatal`
  (permanent).
- `podcast.py:Episode`: the core episode row. It holds identity (`id`,
  `podcast_id`, `external_id`, `canonical_id`, `slug`), feed metadata, the
  pipeline artifact paths (`audio_path`, `downsampled_audio_path`,
  `raw_transcript_path`, `clean_transcript_path`, `clean_transcript_json_path`,
  `summary_path`), `summary_preview`, `published_at`,
  `playback_time_offset_seconds`, and the failure fields (`failed_at_stage`,
  `failure_reason`, `failure_type`, `failed_at`).
- `podcast.py:Episode.state`: a computed field that returns an
  `EpisodeState`. `Episode.is_failed`, `Episode.can_retry`,
  `Episode.last_successful_state`, `Episode.description_text`, and
  `Episode.description_links` are properties.
- `podcast.py:Podcast`: the show row. It holds `rss_url`, `slug`, the
  categories, `apple_url`, `youtube_url`, `spotify_url`, `summary_profile`,
  `last_processed`, `last_processed_at`, `etag`, `last_modified`, and
  `episodes`.
- `podcast.py:TranscriptLink` and `podcast.py:AlternateEnclosure`: the
  Podcasting 2.0 `<podcast:transcript>` and `<podcast:alternateEnclosure>`
  tags. `TranscriptLink.format_extension` maps a MIME type to `srt`, `vtt`,
  `json`, `txt`, or `html`.
- `podcast.py:PlatformLink`, `podcast.py:PlatformLinkCandidate`,
  `PLATFORM_LINK_PLATFORMS` (`apple`, `spotify`, `youtube`),
  `PLATFORM_LINK_MATCH_METHODS` (`guid`, `audio_url`, `title_date`,
  `title_duration`, `publisher`), and `CHANNEL_LISTING_METHODS`: the
  per-episode platform links (spec #87).
- `podcast.py:TranscriptionOperationState` (`pending`, `completed`,
  `downloaded`, `failed`, `cancelled`) and
  `podcast.py:TranscriptionOperation`: Google BatchRecognize tracking.
- `pending_operation.py:PendingOperation`: a frozen dataclass for an
  in-flight async transcription (spec #40). Its fields are `operation_id`,
  `provider`, `episode_id`, `payload`, `created_at`, and `updated_at`.
- `transcription.py:TranscribeOptions`: a dataclass with `language`,
  `episode_id`, `podcast_slug`, `episode_slug`, `audio_url`, and
  `progress_callback`.

### Transcripts (`transcript.py`, `annotated_transcript.py`, `transcript_edits.py`)

- `transcript.py:Word`, `transcript.py:Segment`, and
  `transcript.py:Transcript`: the raw speech-to-text output that every
  transcriber returns. `Transcript` provides `get_speakers`, `get_duration`,
  `adjust_timestamps`, `apply_speaker_mapping`, `get_words_in_range`,
  `build_speaker_mapping`, and `merge`.
- `annotated_transcript.py:SegmentKind`: `content`, `filler`, `ad_break`,
  `music`, `intro`, and `outro`.
- `annotated_transcript.py:WordSpan`, `annotated_transcript.py:AnnotatedSegment`,
  `annotated_transcript.py:CleaningProvenance`, and
  `annotated_transcript.py:AnnotatedTranscript`: the cleaned sidecar (spec #18).
  `AnnotatedTranscript.from_raw` and
  `AnnotatedTranscript.to_blended_markdown` are its entry points.
- `transcript_edits.py:EDIT_OPS`: the closed op list `speaker.rename_label`,
  `speaker.merge_labels`, `segment.set_speaker`, `segment.set_text`,
  `segment.set_kind`, `segment.split`, `segment.merge`, and `revert`.
- `transcript_edits.py:STRUCTURAL_OPS`, `transcript_edits.py:LABEL_OPS`,
  `MAX_SEGMENT_TEXT_CHARS` (20,000), and `MAX_NAME_CHARS` (200).
- `transcript_edits.py:EditOp`: a union discriminated on `op` over
  `SpeakerRenameLabel`, `SpeakerMergeLabels`, `SegmentSetSpeaker`,
  `SegmentSetText`, `SegmentSetKind`, `SegmentSplit`, `SegmentMerge`, and
  `Revert`. Their anchors are `SegmentAnchor`, `LabelAnchor`, `MergeAnchor`,
  and `EditAnchor`.
- `transcript_edits.py:parse_op` validates one `{op, anchor, args}` dict, and
  `transcript_edits.py:text_sha256` hashes segment text.
- `transcript_edits.py:TranscriptEdit`: one row of the `transcript_edits` op
  log. `TranscriptEdit.parsed()` returns the typed op, and
  `TranscriptEdit.is_structural` reports whether the op moves segment ids.

### Facts and entities (`facts.py`, `entities.py`, `enrichment.py`)

- `facts.py:PodcastFacts` and `facts.py:EpisodeFacts`. `EpisodeFacts` maps
  each speaker label to `"Name (Role)"` in `speaker_mapping`.
- `facts.py:strip_role_annotation`, `facts.py:AD_WORD`, and
  `facts.py:ad_voice_names`: speaker-name helpers that detect ad voices.
- `entities.py:EntityType`: `person`, `company`, `product`, and `topic`.
- `entities.py:ResolutionStatus`: `pending`, `resolved`, `unresolvable`,
  `ambiguous`, and `dropped`.
- `entities.py:EntityExtractionStatus`: `pending`, `complete`, `failed`,
  `skipped_legacy`, `linking_deferred`, and `skipped_unavailable`.
- `entities.py:MentionRole`: `host`, `guest`, `mentioned`, `self`, and
  `speaking`.
- `entities.py:ResolutionMethod`: `direct`, `llm_linked`, `anchor`, `coref`,
  `override`, `unresolvable`, and `ambiguous`.
- `entities.py:MatchType`: `lexical`, `semantic`, `hybrid`, and `entity`.
- `entities.py:EntityRecord`, `entities.py:EntityMention`, and
  `entities.py:CitationRow`. `CitationRow` is the wire row that every search
  or list tool returns.
- `entities.py:display_name(name)`: the name without its trailing
  Wikidata-style disambiguation, so `Amazon (company)` becomes `Amazon`.
  Callers include `thestill/core/entity_anchor.py` and
  `thestill/web/routes/api_public.py`.
- `entities.py:strip_honorifics(name)` and `entities.py:HONORIFICS`: the
  name without its leading courtesy or academic titles, so `Professor Mariam
  Jamal-Hanjani` becomes `Mariam Jamal-Hanjani`. Callers include
  `thestill/services/role_linker.py`, `thestill/core/entity_anchor.py`,
  `thestill/core/entity_review.py` and `thestill/core/entity_coref.py`
  (`HONORIFICS` only).
- `enrichment.py:EnrichmentUnavailable` (exception),
  `enrichment.py:EnrichmentStatus` (`pending`, `ok`, `empty`, `failed`),
  `enrichment.py:EntityFact`, `enrichment.py:EntityAffiliation`, and
  `enrichment.py:EntityEnrichment`, which provides `has_content()`.

### Users, auth, and MCP tokens (`user.py`, `oauth.py`, `mcp_token.py`)

- `user.py:DEFAULT_USER_EMAIL` (`local@thestill.me`), `user.py:UserStatus`
  (`active`, `suspended`, `banned`), and `user.py:PRIVATE_USER_FIELDS`.
- `user.py:User`, which provides the `is_active` property.
  `user.py:TokenPayload` holds the JWT claims `sub`, `exp`, `iat`, and `jti`,
  and `user.py:PodcastFollower` is a follow relationship.
- `mcp_token.py:SCOPE_READ`, `SCOPE_FOLLOWS`, `SCOPE_PIPELINE`, and
  `ALL_SCOPES`, which are `read`, `follows`, and `pipeline`.
- `mcp_token.py:McpToken`, which provides `is_active(now)`.
- `oauth.py:SCOPE_OFFLINE_ACCESS`, `ClientSource` (`cimd` or `dcr`),
  `TokenKind` (`access` or `refresh`), and `RevokeReason` (`user`, `admin`,
  `replaced`, or `reuse`).
- `oauth.py:OAuthClientRecord`, `OAuthPending`, `OAuthGrant`, `OAuthCode`, and
  `OAuthTokenRecord`.

### Inbox, listening, and briefings

- `inbox.py:InboxSource` (`follow_new`, `follow_seed`, `ad_hoc`, `import`),
  `inbox.py:InboxState` (`unread`, `read`, `saved`, `dismissed`),
  `INBOX_SOURCES`, `INBOX_STATES`, and `INBOX_STATES_ELIGIBLE_FOR_BRIEFING`.
- `inbox.py:InboxEntry`, `PodcastInboxSummary`, `ArrivingItem` (spec #88), and
  `InboxItem`.
- `listening.py:ListeningEvent`: `tick`, `pause`, `seek`, `ended`, `stop`,
  `mark_played`, and `mark_unplayed`.
- `listening.py:MANUAL_EVENTS`, `PLAYBACK_EVENTS`, `COMPLETION_TAIL_SECONDS`
  (45.0), and `CLAMP_SLACK_SECONDS` (5.0).
- `listening.py:ListeningProgress`, `ListeningSummary` (provides
  `from_columns`), `ProgressWrite`, `WriteOutcome`, and `ResumableEpisode`.
- `briefing.py:Briefing`.
- `briefing_delivery.py:DeliveryChannel` (`email`),
  `briefing_delivery.py:DeliveryStatus` (`pending`, `sending`, `sent`,
  `failed`), and `briefing_delivery.py:BriefingDelivery`.
- `briefing_schedule.py:BriefingFrequency` (`daily`, `weekly`) and
  `briefing_schedule.py:BriefingSchedule`, which provides the `tzinfo`
  property.

### Analytics payloads (`analytics.py`)

- `analytics.py:AnalyticsReport`, the top-level payload, which contains
  `ScorecardReport`, `SignupsReport`, `ActivationReport`, `CohortsReport`,
  `EngagementReport`, `ContentReport`, and `TrafficReport`.
- `analytics.py:STEPS`, `StepDef`, `SCORECARD_METRICS`, `MetricDef`,
  `ACTIVATION_HOURS` (168), `COHORT_WEEKS` (1, 2, 4, 8), `COHORT_ROWS` (12),
  `SCORECARD_WEEKS` (8), `ENGAGED_DAYS` (2), `RETENTION_WEEK` (4),
  `SMALL_GROUP` (5), `PUBLIC_KINDS`, `BEFORE_TRACKING`, and `UNCATEGORISED`.
- `analytics.py:change_label` and `analytics.py:ratio`: the shared
  formatters.

## Invariants

- `Episode.state` is derived and never stored. A truthy `failed_at_stage`
  wins. Otherwise the furthest artifact path sets the state:
  `summary_path`, then `clean_transcript_path`, then `raw_transcript_path`,
  then `downsampled_audio_path`, then `audio_path`. Only
  `clean_transcript_path`, not `clean_transcript_json_path`, makes an episode
  CLEANED [`podcast.py:Episode.state`].
- Every failed episode is retryable, whatever its `failure_type`
  [`podcast.py:Episode.can_retry`].
- A missing `Episode.slug` or `Podcast.slug` is generated from the title
  [`podcast.py:Episode.ensure_slug`, `podcast.py:Podcast.ensure_slug`].
- `Episode.pub_date`, `Podcast.last_processed`, and
  `Podcast.last_processed_at` are coerced to timezone-aware UTC
  [`podcast.py:Episode.ensure_pub_date_aware`,
  `podcast.py:Podcast.ensure_last_processed_aware`].
- `PlatformLink.platform` must be in `PLATFORM_LINK_PLATFORMS`, and
  `match_method` must be `None` or in `PLATFORM_LINK_MATCH_METHODS`
  [`podcast.py:PlatformLink._validate_enums`].
- `AlternateEnclosure.source_uri` is a plain `str` so that non-HTTP URIs are
  allowed. `TranscriptLink.url`, `Episode.audio_url`, and `Podcast.rss_url`
  are validated as `HttpUrl` [`podcast.py:AlternateEnclosure`].
- `PendingOperation` is immutable (`frozen=True`)
  [`pending_operation.py:PendingOperation`].
- `transcript_edits` rows are append-only: an undo is a new `revert` row
  [`transcript_edits.py:TranscriptEdit`]. `segment.adjust_boundary` is in
  `STRUCTURAL_OPS` but not in `EDIT_OPS` or the `EditOp` union, so
  `parse_op` rejects it [`transcript_edits.py:EDIT_OPS`].
- `SegmentAnchor` needs at least one `source_segment_ids` entry and a
  64-hex-character `text_sha256`. A raw `text` key is hashed and dropped
  before validation, so a row never stores the segment text
  [`transcript_edits.py:SegmentAnchor._hash_text`].
- `SetSpeakerArgs` needs exactly one of `label` or `name`
  [`transcript_edits.py:SetSpeakerArgs._one_of`]. `RenameLabelArgs.name` and
  `SetTextArgs.new_text` must not be blank, and a label cannot be merged into
  itself [`transcript_edits.py:RenameLabelArgs._strip`,
  `SetTextArgs._not_blank`, `SpeakerMergeLabels._distinct`].
- Edit text is limited to `MAX_SEGMENT_TEXT_CHARS` and names to
  `MAX_NAME_CHARS`. `SplitArgs.text_word_index` must be at least 1
  [`transcript_edits.py:SplitArgs`].
- `AnnotatedTranscript.from_raw` maps each raw segment to exactly one
  `kind="content"` segment, with `source_segment_ids=[seg.id]`
  [`annotated_transcript.py:AnnotatedTranscript.from_raw`].
- `to_blended_markdown` always drops `filler` segments and starts a new
  timecoded block on each speaker change or every 300 s. For a `from_raw`
  wrapper, its output is byte-identical to the legacy
  `TranscriptFormatter` output
  [`annotated_transcript.py:AnnotatedTranscript.to_blended_markdown`,
  `_BLENDED_TIMECODE_INTERVAL_SECONDS`].
- `BriefingSchedule.hour_local` is 0–23 and `weekday` is 0–6.
  `weekday` is required when the frequency is `weekly` and must be `None`
  when it is `daily`. `timezone_name` must be a valid IANA zone; there is no
  silent fallback to UTC [`briefing_schedule.py:BriefingSchedule._validate_timezone`,
  `_weekday_iff_weekly`].
- `BriefingDelivery.attempts` is at least 0
  [`briefing_delivery.py:BriefingDelivery`].
- `INBOX_SOURCES` and `INBOX_STATES` come from `get_args` on the `Literal`
  types, so they cannot drift from them. Only `dismissed` entries are
  excluded from briefings [`inbox.py:INBOX_STATES_ELIGIBLE_FOR_BRIEFING`].
- `ListeningSummary.from_columns` returns `None` when `updated_at` is `None`
  [`listening.py:ListeningSummary.from_columns`]. The public summary never
  carries `session_id`, `seq`, `stopped_at`, or `listened_at`
  [`listening.py:ListeningProgress.summary`].
- `McpToken.is_active` is false once `revoked_at` is set or `expires_at` has
  passed. Scopes default to `("read",)` [`mcp_token.py:McpToken`].
- `User.is_active` is true only for `UserStatus.ACTIVE`. The API never
  returns `PRIVATE_USER_FIELDS` to the account owner
  [`user.py:User`, `user.py:PRIVATE_USER_FIELDS`].
- `ratio` returns `None` when the denominator is zero and otherwise rounds to
  4 places. `change_label` returns `new`, `—`, `0`, or a signed difference
  that uses the minus sign U+2212 [`analytics.py:ratio`,
  `analytics.py:change_label`].
- `ad_voice_names` leaves out any name that another speaker also carries
  without an ad role [`facts.py:ad_voice_names`].
- `display_name` removes only one parenthetical, and only at the end of the
  name, with any whitespace before it (`\s*\([^)]*\)$`). It never returns
  an empty string for a non-empty name: when stripping leaves nothing, it
  returns the original name. `None` becomes `""`
  [`entities.py:display_name`, `entities.py:_DISAMBIGUATION`].
- `strip_honorifics` removes leading `HONORIFICS` tokens (case-insensitive,
  with or without a full stop) only while at least two words remain, so
  `Dr. Dre` and `Lady Gaga` are unchanged. Royal and office titles are not
  in `HONORIFICS`. A name with no title is returned unchanged, spacing
  included. `None` becomes `""` [`entities.py:strip_honorifics`].

## Side Effects

- None at the I/O level. These modules run no SQL and do no file, network,
  logging, or environment work.
- Default factories read the clock (`datetime.now(timezone.utc)`) and
  generate `uuid4` ids [`podcast.py:Episode`, `inbox.py:InboxEntry`,
  `user.py:User`, `briefing.py:Briefing`].
- `BriefingSchedule` validation and its `tzinfo` property read the system
  tz database through `ZoneInfo`
  [`briefing_schedule.py:BriefingSchedule.tzinfo`].

## Verification

- Run: `./venv/bin/pytest tests/unit/models`
- Run: `make test` for the full suite.
