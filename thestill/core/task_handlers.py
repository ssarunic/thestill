# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Task handlers for pipeline operations.

Each handler processes a specific pipeline stage (download, downsample,
transcribe, clean, summarize). Handlers are designed to be called by
the TaskWorker and reuse the same core processing logic as the CLI.

Usage:
    from thestill.core.task_handlers import create_task_handlers

    handlers = create_task_handlers(app_state)
    # handlers is a dict mapping TaskStage -> handler function
"""

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Generator, Optional, Tuple

from structlog import get_logger

from thestill.utils.exceptions import FatalError, TransientError

from ..models.podcast import Episode, Podcast
from ..models.transcription import TranscribeOptions
from ..utils.console import ConsoleOutput
from .audio_downloader import AudioDownloader
from .audio_preprocessor import AudioPreprocessor
from .error_classifier import classify_and_raise
from .progress import ProgressCallback, ProgressUpdate, TranscriptionStage
from .queue_manager import Task, TaskStage
from .transcriber_factory import create_transcriber

if TYPE_CHECKING:
    from ..web.dependencies import AppState

logger = get_logger(__name__)


# =============================================================================
# Helper Functions (DRY - reduce boilerplate across handlers)
# =============================================================================


def _get_episode_or_fail(task: Task, state: "AppState") -> Tuple[Podcast, Episode]:
    """
    Get episode and podcast from task, raising FatalError if not found.

    Args:
        task: Task containing episode_id
        state: Application state with repository

    Returns:
        Tuple of (Podcast, Episode)

    Raises:
        FatalError: If episode not found in database
    """
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise FatalError(f"Episode not found in database: {task.episode_id}")
    return result


@contextmanager
def _handler_error_context(context_msg: str, default_transient: bool = True) -> Generator[None, None, None]:
    """
    Context manager for consistent error handling in task handlers.

    Catches exceptions and classifies them as transient or fatal.
    Already-classified errors (FatalError, TransientError) are re-raised as-is.

    Args:
        context_msg: Context message for error classification
        default_transient: Whether unclassified errors default to transient

    Yields:
        None

    Raises:
        FatalError: If error is classified as fatal
        TransientError: If error is classified as transient
    """
    try:
        yield
    except (FatalError, TransientError):
        raise  # Already classified
    except Exception as e:
        classify_and_raise(e, context=context_msg, default_transient=default_transient)


def _convert_language_for_transcriber(language: str, provider: str) -> str:
    """
    Convert ISO 639-1 language code to format expected by transcriber.

    Different transcription providers expect different language code formats:
    - Whisper/WhisperX: ISO 639-1 (e.g., "en", "hr", "de")
    - ElevenLabs: ISO 639-1 (e.g., "en", "hr", "de")
    - Google Cloud: BCP-47 (e.g., "en-US", "hr-HR", "de-DE")

    Args:
        language: ISO 639-1 code (e.g., "en", "hr", "de")
        provider: Transcription provider name ("whisper", "google", "elevenlabs")

    Returns:
        Language code in provider's expected format
    """
    if provider.lower() == "google":
        # Google Cloud Speech-to-Text uses BCP-47 language codes
        # Map common ISO 639-1 codes to their BCP-47 equivalents
        locale_map = {
            "en": "en-US",
            "hr": "hr-HR",
            "de": "de-DE",
            "es": "es-ES",
            "fr": "fr-FR",
            "it": "it-IT",
            "pt": "pt-BR",
            "nl": "nl-NL",
            "pl": "pl-PL",
            "ru": "ru-RU",
            "ja": "ja-JP",
            "ko": "ko-KR",
            "zh": "zh-CN",
            "ar": "ar-SA",
            "tr": "tr-TR",
            "cs": "cs-CZ",
            "sk": "sk-SK",
            "sl": "sl-SI",
            "sr": "sr-RS",
            "bs": "bs-BA",
            "uk": "uk-UA",
            "hu": "hu-HU",
            "ro": "ro-RO",
            "bg": "bg-BG",
            "el": "el-GR",
            "sv": "sv-SE",
            "da": "da-DK",
            "fi": "fi-FI",
            "no": "nb-NO",
        }
        return locale_map.get(language, f"{language}-{language.upper()}")
    else:
        # Whisper and ElevenLabs use ISO 639-1 codes directly
        return language


def handle_download(task: Task, state: "AppState") -> None:
    """
    Download audio for an episode.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        FatalError: If episode not found in database
        TransientError: If download fails due to network issues
    """
    logger.info(f"Processing download task for episode {task.episode_id}")

    podcast, episode = _get_episode_or_fail(task, state)

    with _handler_error_context(f"downloading audio for {episode.title}"):
        # Create downloader and download
        # Note: download_episode raises DownloadError on failure (no longer returns None)
        downloader = AudioDownloader(
            str(state.path_manager.original_audio_dir()),
            max_bytes=state.config.max_audio_bytes,
        )
        audio_path = downloader.download_episode(episode, podcast)

        # Get duration from downloaded file
        from ..utils.duration import get_audio_duration

        full_audio_path = state.path_manager.original_audio_file(audio_path)
        duration_seconds = get_audio_duration(full_audio_path)

        # Update episode state
        state.feed_manager.mark_episode_downloaded(
            str(podcast.rss_url), episode.external_id, audio_path, duration=duration_seconds
        )

        logger.info(f"Download completed for episode: {episode.title}")


def handle_downsample(task: Task, state: "AppState") -> None:
    """
    Downsample audio to 16kHz, 16-bit, mono WAV.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        FatalError: If episode not found or audio file missing/corrupt
        TransientError: If processing fails due to temporary issues
    """
    logger.info(f"Processing downsample task for episode {task.episode_id}")

    podcast, episode = _get_episode_or_fail(task, state)

    if not episode.audio_path:
        raise FatalError(f"No audio path set for episode: {task.episode_id}")

    # Verify original audio exists
    original_audio_file = state.path_manager.original_audio_file(episode.audio_path)
    if not original_audio_file.exists():
        raise FatalError(f"Original audio file not found: {original_audio_file}")

    # Audio processing errors are usually fatal (corrupt file, unsupported format)
    with _handler_error_context(f"downsampling audio for {episode.title}", default_transient=False):
        # Determine output directory
        audio_path_obj = Path(episode.audio_path)
        if len(audio_path_obj.parts) > 1:
            podcast_subdir = audio_path_obj.parent
            output_dir = state.path_manager.downsampled_audio_dir() / podcast_subdir
        else:
            output_dir = state.path_manager.downsampled_audio_dir() / podcast.slug

        output_dir.mkdir(parents=True, exist_ok=True)

        # Downsample - use structlog logger to avoid stdout conflicts in worker thread
        preprocessor = AudioPreprocessor(logger=logger)
        downsampled_path = preprocessor.downsample_audio(str(original_audio_file), str(output_dir))

        if not downsampled_path:
            raise FatalError(f"Downsampling returned no path for episode: {episode.title}")

        # Store relative path
        downsampled_path_obj = Path(downsampled_path)
        relative_path = f"{output_dir.name}/{downsampled_path_obj.name}"

        # Get duration from downsampled file
        from ..utils.duration import get_audio_duration

        duration_seconds = get_audio_duration(downsampled_path)

        # Update episode state
        state.feed_manager.mark_episode_downsampled(
            str(podcast.rss_url), episode.external_id, relative_path, duration=duration_seconds
        )

        # Auto-cleanup: delete original audio file after successful downsampling
        if state.config.delete_audio_after_processing and episode.audio_path:
            from .audio_downloader import AudioDownloader

            downloader = AudioDownloader(
                str(state.path_manager.original_audio_dir()),
                max_bytes=state.config.max_audio_bytes,
            )
            if downloader.delete_audio_file(episode):
                state.feed_manager.clear_episode_audio_path(str(podcast.rss_url), episode.external_id)
                logger.info(f"Cleaned up original audio file for episode: {episode.title}")

        logger.info(f"Downsample completed for episode: {episode.title}")


def handle_transcribe(
    task: Task,
    state: "AppState",
    progress_callback: ProgressCallback | None = None,
) -> None:
    """
    Transcribe episode audio using configured provider.

    Args:
        task: Task containing episode_id
        state: Application state with services
        progress_callback: Optional callback for progress reporting

    Raises:
        FatalError: If episode not found or audio file missing
        TransientError: If transcription fails due to API/network issues
    """
    logger.info(f"Processing transcribe task for episode {task.episode_id}")

    # Report initial progress
    if progress_callback:
        progress_callback(
            ProgressUpdate(
                stage=TranscriptionStage.PENDING,
                progress_pct=0,
                message="Starting transcription...",
            )
        )

    podcast, episode = _get_episode_or_fail(task, state)

    config = state.config

    # Dalston can fetch audio directly via URL, skipping download/downsample
    use_dalston_url = (
        config.transcription_provider == "dalston" and episode.audio_url and not episode.downsampled_audio_path
    )

    if not use_dalston_url:
        if not episode.downsampled_audio_path:
            raise FatalError(f"No downsampled audio path for episode: {task.episode_id}")

        # Verify audio file exists
        audio_file = config.path_manager.downsampled_audio_file(episode.downsampled_audio_path)
        if not audio_file.exists():
            raise FatalError(f"Downsampled audio file not found: {audio_file}")

    # Transcription errors are usually transient (API issues, rate limits)
    with _handler_error_context(f"transcribing {episode.title}"):
        # Create transcriber based on config (with progress callback if available)
        logger.debug(f"Creating transcriber, provider={config.transcription_provider}")
        transcriber = create_transcriber(
            config,
            config.path_manager,
            progress_callback=progress_callback,
        )
        logger.debug(f"Transcriber created: {type(transcriber).__name__}")

        # Determine output path
        if use_dalston_url:
            podcast_subdir = podcast.slug
            # Build a stable filename from episode slug
            transcript_filename = f"{episode.slug}_transcript.json"
            audio_path_for_transcriber = f"{podcast_subdir}/{episode.slug}"
        else:
            path_parts = Path(episode.downsampled_audio_path).parts
            podcast_subdir = path_parts[0] if len(path_parts) >= 2 else podcast.slug
            transcript_filename = f"{audio_file.stem}_transcript.json"
            audio_path_for_transcriber = str(audio_file)

        transcript_dir = config.path_manager.raw_transcripts_dir() / podcast_subdir
        transcript_dir.mkdir(parents=True, exist_ok=True)

        output = str(transcript_dir / transcript_filename)
        output_db_path = f"{podcast_subdir}/{transcript_filename}"

        # Convert language code to provider-specific format
        language = _convert_language_for_transcriber(podcast.language, config.transcription_provider)
        logger.info(f"Transcribing with language: {language} (podcast language: {podcast.language})")

        # Transcribe
        if use_dalston_url:
            logger.info(f"Starting transcription via URL: {episode.audio_url}")
        else:
            file_size_mb = audio_file.stat().st_size / 1024 / 1024
            logger.info(f"Starting transcription: {audio_file.name} ({file_size_mb:.1f}MB)")

        transcript_data = transcriber.transcribe_audio(
            audio_path_for_transcriber,
            output,
            options=TranscribeOptions(
                language=language,
                episode_id=episode.id,
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                audio_url=str(episode.audio_url) if use_dalston_url else None,
                progress_callback=progress_callback,
            ),
        )
        logger.info(f"Transcription completed, result: {type(transcript_data).__name__}")

        if not transcript_data:
            raise TransientError(f"Transcription returned no data for episode: {episode.title}")

        # Update episode state
        state.feed_manager.mark_episode_processed(
            str(podcast.rss_url),
            episode.external_id,
            raw_transcript_path=output_db_path,
            clean_transcript_path="",  # Clear - needs re-cleaning
            summary_path="",  # Clear - needs re-summarizing
        )

        # Auto-cleanup: delete downsampled audio file after successful transcription
        if config.delete_audio_after_processing and episode.downsampled_audio_path:
            from .audio_preprocessor import AudioPreprocessor

            preprocessor = AudioPreprocessor(logger=logger)
            if preprocessor.delete_downsampled_audio(
                episode.downsampled_audio_path,
                str(config.path_manager.downsampled_audio_dir()),
            ):
                state.feed_manager.clear_episode_downsampled_audio_path(str(podcast.rss_url), episode.external_id)
                logger.info(f"Cleaned up downsampled audio file for episode: {episode.title}")

        logger.info(f"Transcription completed for episode: {episode.title}")


def handle_clean(task: Task, state: "AppState") -> None:
    """
    Clean transcript using LLM.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        FatalError: If episode not found or transcript file missing
        TransientError: If LLM API fails due to rate limits/network issues
    """
    logger.info(f"Processing clean task for episode {task.episode_id}")

    podcast, episode = _get_episode_or_fail(task, state)

    if not episode.raw_transcript_path:
        raise FatalError(f"No raw transcript path for episode: {task.episode_id}")

    config = state.config
    path_manager = state.path_manager

    # Load transcript
    transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
    if not transcript_path.exists():
        raise FatalError(f"Transcript file not found: {transcript_path}")

    # LLM errors are usually transient (rate limits, API issues)
    with _handler_error_context(f"cleaning transcript for {episode.title}"):
        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_data = json.load(f)

        # Create LLM provider
        from .llm_provider import create_llm_provider_from_config
        from .transcript_cleaning_processor import TranscriptCleaningProcessor

        llm_provider = create_llm_provider_from_config(config)

        # Use quiet console to avoid broken pipe errors in web worker context
        cleaning_processor = TranscriptCleaningProcessor(
            llm_provider,
            console=ConsoleOutput(quiet=True),
        )

        # Generate output path
        base_name = transcript_path.stem
        if base_name.endswith("_transcript"):
            base_name = base_name[: -len("_transcript")]

        parts = base_name.split("_")
        if len(parts) >= 3:
            episode_slug_hash = "_".join(parts[1:])
        else:
            episode_slug_hash = base_name

        podcast_subdir = path_manager.clean_transcripts_dir() / podcast.slug
        podcast_subdir.mkdir(parents=True, exist_ok=True)

        cleaned_filename = f"{episode_slug_hash}_cleaned.md"
        cleaned_path = podcast_subdir / cleaned_filename
        clean_transcript_db_path = f"{podcast.slug}/{cleaned_filename}"

        # Clean transcript
        logger.info(f"Cleaning transcript with language: {podcast.language}")
        cleaning_result = cleaning_processor.clean_transcript(
            transcript_data=transcript_data,
            podcast_title=podcast.title,
            podcast_description=podcast.description,
            episode_title=episode.title,
            episode_description=episode.description,
            podcast_slug=podcast.slug,
            episode_slug=episode.slug,
            output_path=str(cleaned_path),
            path_manager=path_manager,
            language=podcast.language,
        )

        if not cleaning_result:
            raise TransientError(f"Transcript cleaning returned no result for episode: {episode.title}")

        # Persist the segmented-JSON sidecar path when the segmented
        # pipeline produced one (see thestill/cli.py:694 for the
        # canonical pattern). Without this the UI can't find the
        # structured transcript even though the file is on disk.
        clean_transcript_json_db_path: Optional[str] = None
        if cleaning_result.get("cleaned_json_path"):
            json_filename = f"{Path(cleaned_filename).stem}.json"
            clean_transcript_json_db_path = f"{podcast.slug}/{json_filename}"

        # Update episode state
        state.feed_manager.mark_episode_processed(
            str(podcast.rss_url),
            episode.external_id,
            raw_transcript_path=episode.raw_transcript_path,
            clean_transcript_path=clean_transcript_db_path,
            clean_transcript_json_path=clean_transcript_json_db_path,
        )

        logger.info(f"Transcript cleaning completed for episode: {episode.title}")


def handle_summarize(task: Task, state: "AppState") -> None:
    """
    Summarize transcript using LLM.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        FatalError: If episode not found or transcript file missing
        TransientError: If LLM API fails due to rate limits/network issues
    """
    logger.info(f"Processing summarize task for episode {task.episode_id}")

    podcast, episode = _get_episode_or_fail(task, state)

    if not episode.clean_transcript_path:
        raise FatalError(f"No clean transcript path for episode: {task.episode_id}")

    config = state.config
    path_manager = state.path_manager

    # Load clean transcript
    clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
    if not clean_path.exists():
        raise FatalError(f"Clean transcript file not found: {clean_path}")

    # LLM errors are usually transient (rate limits, API issues)
    with _handler_error_context(f"summarizing {episode.title}"):
        with open(clean_path, "r", encoding="utf-8") as f:
            transcript_text = f.read()

        # Create LLM provider and summarizer
        from .llm_provider import create_llm_provider_from_config
        from .post_processor import EpisodeMetadata, TranscriptSummarizer

        llm_provider = create_llm_provider_from_config(config)

        # Use quiet console to avoid broken pipe errors in web worker context
        summarizer = TranscriptSummarizer(llm_provider, console=ConsoleOutput(quiet=True))

        # Create metadata for accurate summary
        metadata = EpisodeMetadata(
            title=episode.title,
            pub_date=episode.pub_date,
            duration_seconds=episode.duration,
            podcast_title=podcast.title,
        )

        # Determine output path - preserve podcast subfolder structure
        clean_transcripts_dir = path_manager.clean_transcripts_dir().resolve()
        clean_path_resolved = clean_path.resolve()

        try:
            relative_path = clean_path_resolved.relative_to(clean_transcripts_dir)
            if len(relative_path.parts) > 1:
                podcast_slug = relative_path.parts[0]
                summary_filename = f"{clean_path.stem}_summary.md"
                output_path = path_manager.summaries_dir() / podcast_slug / summary_filename
                summary_db_path = f"{podcast_slug}/{summary_filename}"
            else:
                summary_filename = f"{clean_path.stem}_summary.md"
                output_path = path_manager.summary_file(summary_filename)
                summary_db_path = summary_filename
        except ValueError:
            summary_filename = f"{clean_path.stem}_summary.md"
            output_path = path_manager.summary_file(summary_filename)
            summary_db_path = summary_filename

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Summarize
        summarizer.summarize(transcript_text, output_path, metadata=metadata)

        # Update episode state
        state.feed_manager.mark_episode_processed(
            str(podcast.rss_url),
            episode.external_id,
            raw_transcript_path=episode.raw_transcript_path,
            clean_transcript_path=episode.clean_transcript_path,
            summary_path=summary_db_path,
        )

        logger.info(f"Summarization completed for episode: {episode.title}")


def handle_entity_branch_placeholder(task: Task, state: "AppState") -> None:
    """Spec #28 §0.5 — shared no-op handler for entity stages still in Phase 0 mode.

    Phase 1 replaces these one-by-one with real handlers (GLiNER
    extractor, ReFinED resolver, corpus writer, qmd reindexer). Until
    each stage's real handler lands, the no-op keeps the linear chain
    progressing through to ``REINDEX`` without hitting "no handler
    registered" → DLQ. ``task.stage.value`` distinguishes the call
    sites in structured logs.
    """
    logger.info(
        "entity_branch_placeholder",
        stage=task.stage.value,
        episode_id=task.episode_id,
        note="phase_0_no_op",
    )


def handle_extract_entities(task: Task, state: "AppState") -> None:
    """Spec #28 §1.2 — run GLiNER over the cleaned-transcript JSON sidecar.

    Reads ``episodes.clean_transcript_json_path``. Episodes without a
    sidecar (legacy Markdown-only) are flagged as ``skipped_legacy``
    and emit zero mentions — re-cleaning them through the
    segment-preserving pipeline is a separate spec.

    Idempotent: re-running on an already-extracted episode wipes the
    old ``entity_mentions`` for that episode and writes fresh ones.
    The user-facing failure isolation rule (``_NON_USER_FAILING_STAGES``
    in queue_manager) means errors here flip
    ``episodes.entity_extraction_status='failed'`` rather than the
    user-visible ``failed_at_stage``.
    """
    from ..models.entities import EntityExtractionStatus

    logger.info("entity_extraction_started", episode_id=task.episode_id)

    podcast, episode = _get_episode_or_fail(task, state)
    repo = state.entity_repository

    if not episode.clean_transcript_json_path:
        # Legacy episode — Markdown-only cleaning, no structured
        # sidecar. Spec is explicit: skip with the documented status,
        # never raise.
        state.repository.update_entity_extraction_status(
            episode_id=episode.id,
            status=EntityExtractionStatus.SKIPPED_LEGACY.value,
        )
        logger.info(
            "entity_extraction_skipped_legacy",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
        )
        return

    sidecar_path = state.path_manager.clean_transcript_file(episode.clean_transcript_json_path)
    if not sidecar_path.exists():
        raise FatalError(f"AnnotatedTranscript sidecar not found: {sidecar_path} (episode {episode.id})")

    state.repository.update_entity_extraction_status(
        episode_id=episode.id,
        status=EntityExtractionStatus.PENDING.value,
    )

    with _handler_error_context(f"extracting entities for {episode.title}"):
        from ..models.annotated_transcript import AnnotatedTranscript

        transcript = AnnotatedTranscript.model_validate_json(sidecar_path.read_text(encoding="utf-8"))

        # Spec §1.13.4 — load anchor entities (host/guest/recurring) for
        # this episode and expand them into surface variants. Empty list
        # is fine: the extractor short-circuits the anchor-scan and
        # speaker-resolution steps when no anchors are configured.
        from .entity_anchor import expand_anchor_variants

        anchor_ids = repo.get_episode_anchors(episode.id)
        anchor_entities = [e for e in (repo.get_entity(eid) for eid in anchor_ids) if e is not None]
        anchor_variants = expand_anchor_variants(anchor_entities)

        extractor = _get_or_create_entity_extractor(state)
        mentions = extractor.extract(
            transcript,
            episode_id=episode.id,
            anchor_variants=anchor_variants,
        )

        # Idempotent re-extract: wipe + write. The brief gap between
        # the two transactions is harmless because no consumer reads
        # ``entity_mentions`` during ``extract-entities`` — resolution
        # runs in the next stage.
        repo.delete_mentions_for_episode(episode.id)
        inserted = repo.insert_mentions(mentions)

        state.repository.update_entity_extraction_status(
            episode_id=episode.id,
            status=EntityExtractionStatus.COMPLETE.value,
        )
        logger.info(
            "entity_extraction_completed",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
            mentions=inserted,
        )


_extractor_init_lock = threading.Lock()
_resolver_init_lock = threading.Lock()


def handle_resolve_entities(task: Task, state: "AppState") -> None:
    """Spec #28 §1.5 — resolve pending mentions to Wikidata entities.

    Reads all ``resolution_status='pending'`` mentions for the task's
    episode, runs ReFinED, upserts the resulting ``EntityRecord``s,
    flips each mention's status to ``resolved`` or ``unresolvable``,
    runs an inline scoped alias-merge for the touched entities (spec
    §1.6), then triggers an episode-scoped co-occurrence rebuild
    (spec §1.7).

    Idempotent: re-running on an episode whose mentions are already
    resolved returns immediately because ``list_pending_mentions``
    returns []. Use ``thestill rebuild-entities`` (Phase 3) or flip
    rows back to ``pending`` to force re-resolution.

    Failure isolation: errors here flip
    ``episodes.entity_extraction_status='failed'`` (via the worker's
    ``_NON_USER_FAILING_STAGES`` rule), never ``failed_at_stage``.
    """
    logger.info("entity_resolution_started", episode_id=task.episode_id)

    podcast, episode = _get_episode_or_fail(task, state)
    repo = state.entity_repository

    pending = repo.list_pending_mentions(episode_id=episode.id)
    if not pending:
        logger.info(
            "entity_resolution_no_pending",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
        )
        return

    with _handler_error_context(f"resolving entities for {episode.title}"):
        resolver = _get_or_create_entity_resolver(state)

        # Spec §1.13.7 — overrides apply BEFORE we even hand the
        # mention to ReFinED. Pre-route mentions matching a stored
        # override into their forced bucket; only un-overridden rows
        # go through model resolution. This is the "human corrections
        # survive reindex" invariant.
        forced_results, remaining = _apply_overrides(repo, pending)

        # The resolver consults the blacklist for every QID candidate
        # before accepting it (see ``EntityResolver.resolve``).
        resolver_results = resolver.resolve(remaining, is_blacklisted=repo.is_blacklisted)

        results = forced_results + resolver_results

        touched_entity_ids: set[str] = set()
        for r in results:
            repo.upsert_entity(r.entity)
            entity_id_for_mention = r.entity.id if r.status == "resolved" else None
            repo.resolve_mention(
                mention_id=r.mention_id,
                entity_id=entity_id_for_mention,
                status=r.status,
                method=r.method.value,
            )
            touched_entity_ids.add(r.entity.id)

        # Spec §1.13.5 — within-episode coref pass. Walks unresolved
        # person mentions, looks for a single resolved long-form
        # anchor whose canonical name contains the surface as a
        # token, and either repoints (RESOLVED) or marks AMBIGUOUS.
        from .entity_coref import resolve_coreferences_for_episode

        coref_decisions = resolve_coreferences_for_episode(repo, episode.id)

        # Spec §1.6 — inline scoped alias-merge for the entities just
        # touched. Cheap (only checks duplicates whose QID matches one
        # of the entities resolved in this episode); the full-corpus
        # sweep runs via ``thestill merge-aliases``.
        merged = _merge_qid_duplicates_for(repo, touched_entity_ids)
        if merged:
            logger.info("alias_merge_inline", merged_pairs=merged, episode_id=episode.id)

        # Spec §1.7 — refresh cooccurrences for any pair touching this
        # episode's entities. Per spec, the table holds corpus-wide
        # episode_count per pair, so the rebuild has to recompute
        # full counts (it does that internally — see
        # ``rebuild_cooccurrences`` docstring).
        repo.rebuild_cooccurrences(episode_ids=[episode.id])

        logger.info(
            "entity_resolution_completed",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
            mentions=len(results),
            resolved=sum(1 for r in results if r.status == "resolved"),
            unresolvable=sum(1 for r in results if r.status == "unresolvable"),
            override_forced=len(forced_results),
            coref_decisions=len(coref_decisions),
        )


def _apply_overrides(repo, mentions):
    """Spec §1.13.7 — split mentions into (forced_results, remaining).

    Forced results bypass ReFinED entirely: the human override pre-
    determines the outcome, so we build a ``ResolutionResult`` straight
    away. Remaining mentions go through the model.
    """
    from ..models.entities import EntityRecord, EntityType, ResolutionMethod
    from .entity_resolver import ResolutionResult, _build_entity_id

    forced: list = []
    remaining: list = []
    for mention in mentions:
        override = repo.lookup_override(mention.surface_form, mention.episode_id)
        if override is None:
            remaining.append(mention)
            continue
        kind = override["override_kind"]
        if kind == "drop":
            forced.append(
                ResolutionResult(
                    mention_id=mention.id,
                    entity=_local_unresolvable_entity(mention),
                    status="dropped",
                    method=ResolutionMethod.OVERRIDE,
                )
            )
        elif kind == "force_unresolvable":
            forced.append(
                ResolutionResult(
                    mention_id=mention.id,
                    entity=_local_unresolvable_entity(mention),
                    status="unresolvable",
                    method=ResolutionMethod.OVERRIDE,
                )
            )
        elif kind == "force_entity":
            target = repo.get_entity(override["entity_id"])
            if target is None:
                # Operator forced an entity that no longer exists;
                # fall back to model resolution rather than corrupt.
                logger.warning(
                    "override_target_missing",
                    surface_form=mention.surface_form,
                    target_entity_id=override["entity_id"],
                )
                remaining.append(mention)
                continue
            forced.append(
                ResolutionResult(
                    mention_id=mention.id,
                    entity=target,
                    status="resolved",
                    method=ResolutionMethod.OVERRIDE,
                )
            )
        else:  # pragma: no cover — CHECK constraint covers this
            remaining.append(mention)
    return forced, remaining


def _local_unresolvable_entity(mention):
    """Build the local-slug fallback ``EntityRecord`` used for dropped
    or force-unresolvable mentions. Mirrors
    ``EntityResolver._unresolvable_result`` but lives here so the
    handler can fabricate it without a model load.
    """
    from ..models.entities import EntityRecord, EntityType
    from .entity_resolver import SURFACE_LABEL_TO_ENTITY_TYPE, _build_entity_id

    inferred = SURFACE_LABEL_TO_ENTITY_TYPE.get((mention.surface_label or "").lower()) or EntityType.TOPIC
    return EntityRecord(
        id=_build_entity_id(inferred, mention.surface_form, qid=None),
        type=inferred,
        canonical_name=mention.surface_form,
        wikidata_qid=None,
        aliases=[],
    )


def _merge_qid_duplicates_for(repo, touched_entity_ids: set) -> int:
    """Collapse any duplicate-QID pairs that touch ``touched_entity_ids``.

    The full-corpus duplicate scan in
    ``find_duplicate_qid_pairs`` is cheap (one indexed query); the
    filter here is just to keep the inline log noise scoped to "what
    this resolve call actually changed". Returns the number of
    loser-entities deleted.
    """
    pairs = repo.find_duplicate_qid_pairs()
    relevant = [(qid, k, l) for (qid, k, l) in pairs if k in touched_entity_ids or l in touched_entity_ids]
    merged = 0
    for _qid, keeper, loser in relevant:
        repo.repoint_mentions(from_entity_id=loser, to_entity_id=keeper)
        repo.delete_entity(loser)
        merged += 1
    return merged


def _get_or_create_entity_extractor(state: "AppState"):
    """Lazy-init the process-scope ``EntityExtractor``.

    Loading GLiNER costs ~5-10s and ~400MB of RAM, so we defer until
    the first ``extract-entities`` call and cache the instance on
    ``AppState``. Subsequent tasks reuse the warm model.

    The lock prevents two concurrent ``extract-entities`` tasks (when
    ``EXTRACT_ENTITIES_PARALLEL_JOBS > 1``) from both creating fresh
    extractors and double-loading the model — losing ~400MB to a GC'd
    duplicate.
    """
    with _extractor_init_lock:
        if state.entity_extractor is None:
            from .entity_extractor import EntityExtractor

            state.entity_extractor = EntityExtractor()
    return state.entity_extractor


def _get_or_create_entity_resolver(state: "AppState"):
    """Lazy-init the process-scope ``EntityResolver``.

    ReFinED loads several GB of LMDB-indexed Wikidata on first use
    (~30-60s, ~4-6GB RAM). Same lock pattern as the extractor: defer
    until the first ``resolve-entities`` task fires, cache on
    ``AppState``, prevent concurrent double-load.
    """
    with _resolver_init_lock:
        if state.entity_resolver is None:
            from .entity_resolver import EntityResolver

            state.entity_resolver = EntityResolver()
    return state.entity_resolver


def create_task_handlers(
    state: "AppState",
) -> Dict[TaskStage, Callable[[Task, ProgressCallback | None], None]]:
    """
    Create task handlers dictionary with AppState closure.

    Args:
        state: Application state with services

    Returns:
        Dictionary mapping TaskStage to handler function.
        Handlers accept (task, progress_callback) where progress_callback is optional.
    """
    return {
        TaskStage.DOWNLOAD: lambda task, cb=None: handle_download(task, state),
        TaskStage.DOWNSAMPLE: lambda task, cb=None: handle_downsample(task, state),
        TaskStage.TRANSCRIBE: lambda task, cb=None: handle_transcribe(task, state, cb),
        TaskStage.CLEAN: lambda task, cb=None: handle_clean(task, state),
        TaskStage.SUMMARIZE: lambda task, cb=None: handle_summarize(task, state),
        TaskStage.EXTRACT_ENTITIES: lambda task, cb=None: handle_extract_entities(task, state),
        TaskStage.RESOLVE_ENTITIES: lambda task, cb=None: handle_resolve_entities(task, state),
        TaskStage.WRITE_CORPUS: lambda task, cb=None: handle_entity_branch_placeholder(task, state),
        TaskStage.REINDEX: lambda task, cb=None: handle_entity_branch_placeholder(task, state),
    }
