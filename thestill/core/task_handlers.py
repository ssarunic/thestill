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
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Generator, Optional, Tuple

from structlog import get_logger

from thestill.models.transcript import Transcript
from thestill.utils.exceptions import FatalError, TransientError


def _transcript_to_json(transcript: Transcript) -> str:
    """Spec #35 — single JSON encoding for transcripts persisted via FileStorage.

    Used by ``handle_transcribe`` and the equivalent CLI / MCP paths so the
    on-disk shape stays identical across entry points.

    ``mode="json"`` coerces non-JSON-native values (e.g. ``UUID`` job ids that
    Dalston stores in ``provider_metadata``, datetimes) to strings, so the
    subsequent ``json.dumps`` cannot raise "Object of type UUID is not JSON
    serializable" and silently fail a transcription that actually succeeded.
    """
    return json.dumps(transcript.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _existing_transcript_is_valid(config, relative_transcript_path: str) -> bool:
    """Return ``True`` only if a *complete, parseable* transcript already exists.

    ``handle_transcribe`` writes the artifact before the DB row update, and the
    local backend's ``write_text`` is not atomic, so a crash mid-write could
    leave a truncated file. We therefore validate that the bytes round-trip
    through ``Transcript`` before trusting the artifact for resume — a partial
    or corrupt file falls through to a fresh transcription rather than poisoning
    the downstream clean/summarize stages.
    """
    if not config.file_storage.exists(relative_transcript_path):
        return False
    try:
        Transcript.model_validate_json(config.file_storage.read_text(relative_transcript_path))
        return True
    except Exception as error:  # pylint: disable=broad-except
        logger.warning(
            "Existing transcript artifact is invalid; will re-transcribe",
            transcript_path=relative_transcript_path,
            error=str(error),
        )
        return False


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


def convert_language_for_transcriber(language: str, provider: str) -> str:
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

    Spec #35 — the AudioDownloader still writes to local disk because the
    streaming HTTP + yt-dlp paths require a real filesystem destination. We
    point it at a tempdir, then upload the result to the configured backend.
    For ``STORAGE_BACKEND=local`` the tempdir + upload pair degenerates to
    a copy within the data root (still cheap on the same filesystem).
    """
    logger.info(f"Processing download task for episode {task.episode_id}")

    podcast, episode = _get_episode_or_fail(task, state)

    with _handler_error_context(f"downloading audio for {episode.title}"):
        from ..utils.duration import get_audio_duration

        with tempfile.TemporaryDirectory(prefix="thestill_download_") as work_dir:
            downloader = AudioDownloader(
                work_dir,
                max_bytes=state.config.max_audio_bytes,
            )
            # download_episode returns a path relative to ``work_dir`` shaped
            # like "podcast-slug/episode.mp3"; that same shape is the
            # FileStorage key we upload to.
            audio_path = downloader.download_episode(episode, podcast)
            local_audio_file = Path(work_dir) / audio_path

            target_path = state.path_manager.original_audio_file(audio_path)
            state.config.file_storage.upload_file(local_audio_file, state.path_manager.to_relative(target_path))
            duration_seconds = get_audio_duration(local_audio_file)

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

    # Verify original audio exists in the configured backend. Spec #35 — was
    # ``original_audio_file.exists()`` (filesystem-only); the FileStorage
    # exists check is one HeadObject on S3 vs. a real ``stat`` on local.
    original_audio_file = state.path_manager.original_audio_file(episode.audio_path)
    original_audio_key = state.path_manager.to_relative(original_audio_file)
    if not state.config.file_storage.exists(original_audio_key):
        raise FatalError(f"Original audio file not found: {original_audio_file}")

    # Audio processing errors are usually fatal (corrupt file, unsupported format)
    with _handler_error_context(f"downsampling audio for {episode.title}", default_transient=False):
        # Determine output filename + storage-relative key.
        audio_path_obj = Path(episode.audio_path)
        if len(audio_path_obj.parts) > 1:
            podcast_subdir = audio_path_obj.parent
        else:
            podcast_subdir = Path(podcast.slug)
        downsampled_filename = f"{Path(episode.audio_path).stem}.wav"
        downsampled_full_path = state.path_manager.downsampled_audio_dir() / podcast_subdir / downsampled_filename
        downsampled_key = state.path_manager.to_relative(downsampled_full_path)
        relative_path = f"{podcast_subdir}/{downsampled_filename}"

        # Skip if already downsampled — ``exists()`` is one HeadObject on S3
        # which is much cheaper than running the pydub conversion twice.
        if state.config.file_storage.exists(downsampled_key):
            logger.info(f"Downsampled audio already exists, skipping: {relative_path}")
            from ..utils.duration import get_audio_duration

            with state.config.file_storage.local_copy(downsampled_key) as wav_path:
                duration_seconds = get_audio_duration(str(wav_path))
            state.feed_manager.mark_episode_downsampled(
                str(podcast.rss_url), episode.external_id, relative_path, duration=duration_seconds
            )
            return

        # Materialise the input audio for pydub (real filesystem path required)
        # and write the output to a tempdir; the FileStorage upload below moves
        # it to the configured backend.
        preprocessor = AudioPreprocessor(logger=logger)
        with (
            state.config.file_storage.local_copy(original_audio_key) as input_path,
            tempfile.TemporaryDirectory(prefix="thestill_downsample_") as work_dir,
        ):
            tmp_output = preprocessor.downsample_audio(str(input_path), work_dir)
            if not tmp_output:
                raise FatalError(f"Downsampling returned no path for episode: {episode.title}")
            state.config.file_storage.upload_file(tmp_output, downsampled_key)

            from ..utils.duration import get_audio_duration

            duration_seconds = get_audio_duration(tmp_output)

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

    audio_key: Optional[str] = None
    audio_file = None
    if not use_dalston_url:
        if not episode.downsampled_audio_path:
            raise FatalError(f"No downsampled audio path for episode: {task.episode_id}")

        # Verify audio file exists via FileStorage (one HeadObject on S3).
        audio_file = config.path_manager.downsampled_audio_file(episode.downsampled_audio_path)
        audio_key = config.path_manager.to_relative(audio_file)
        if not config.file_storage.exists(audio_key):
            raise FatalError(f"Downsampled audio file not found: {audio_file}")

    # Transcription errors are usually transient (API issues, rate limits)
    with _handler_error_context(f"transcribing {episode.title}"):
        # Determine output path. Computed before the (expensive) provider call
        # so we can probe for an already-persisted artifact and resume.
        if use_dalston_url:
            podcast_subdir = podcast.slug
            transcript_filename = f"{episode.slug}_transcript.json"
        else:
            path_parts = Path(episode.downsampled_audio_path).parts
            podcast_subdir = path_parts[0] if len(path_parts) >= 2 else podcast.slug
            transcript_filename = f"{audio_file.stem}_transcript.json"

        transcript_path = config.path_manager.raw_transcripts_dir() / podcast_subdir / transcript_filename
        output_db_path = f"{podcast_subdir}/{transcript_filename}"
        relative_transcript_path = config.path_manager.to_relative(transcript_path)

        # Idempotent resume: the transcript artifact is written to durable
        # storage *before* the DB row is updated, so a transient failure in
        # that DB write (e.g. ``database is locked``) leaves a complete
        # transcript on disk. Re-transcribing on retry would throw away a
        # provider call that already succeeded — for long episodes that is a
        # ~20-minute Dalston job re-run, and re-running it has repeatedly
        # tripped network timeouts that exhaust the retry budget and fail an
        # episode whose transcript was already in hand. If a valid artifact
        # already exists, skip straight to the persist step.
        if _existing_transcript_is_valid(config, relative_transcript_path):
            logger.info(
                "Reusing existing transcript artifact; skipping re-transcription",
                episode_id=episode.id,
                transcript_path=output_db_path,
            )
        else:
            # Create transcriber based on config (with progress callback if available)
            logger.debug(f"Creating transcriber, provider={config.transcription_provider}")
            transcriber = create_transcriber(
                config,
                config.path_manager,
                pending_ops_repository=getattr(state, "pending_ops_repository", None),
                progress_callback=progress_callback,
            )
            logger.debug(f"Transcriber created: {type(transcriber).__name__}")

            language = convert_language_for_transcriber(podcast.language, config.transcription_provider)
            logger.info(f"Transcribing with language: {language} (podcast language: {podcast.language})")

            # Spec #35 — materialise audio for transcribers via ``local_copy``.
            # On the local backend this is the real path (no copy); on S3 it
            # downloads to a tempfile and cleans up on context exit. Dalston's
            # URL-fetch mode skips local audio entirely.
            audio_context = (
                nullcontext(enter_result=None) if use_dalston_url else config.file_storage.local_copy(audio_key)
            )

            with audio_context as materialised_audio:
                if use_dalston_url:
                    logger.info(f"Starting transcription via URL: {episode.audio_url}")
                    audio_path_for_transcriber = f"{podcast_subdir}/{episode.slug}"
                else:
                    audio_path_for_transcriber = str(materialised_audio)
                    file_size_mb = materialised_audio.stat().st_size / 1024 / 1024
                    logger.info(f"Starting transcription: {materialised_audio.name} ({file_size_mb:.1f}MB)")

                transcript_data = transcriber.transcribe_audio(
                    audio_path_for_transcriber,
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

            # Persist the returned Transcript via FileStorage so the artefact
            # lands on the configured backend.
            config.file_storage.write_text(
                relative_transcript_path,
                _transcript_to_json(transcript_data),
            )

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

    # Load transcript via FileStorage (spec #35). FileNotFoundError replaces
    # the prior exists()+open pair.
    transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
    transcript_key = path_manager.to_relative(transcript_path)

    # LLM errors are usually transient (rate limits, API issues)
    with _handler_error_context(f"cleaning transcript for {episode.title}"):
        try:
            transcript_payload = config.file_storage.read_text(transcript_key)
        except FileNotFoundError:
            raise FatalError(f"Transcript file not found: {transcript_path}")
        transcript_data = json.loads(transcript_payload)

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

        # Bridge LLM-extracted host/guest names → entity layer.
        # Best-effort: a parse failure here shouldn't fail the
        # cleaning task. The facts files were just produced by the
        # cleaning pass above, so they're guaranteed fresh.
        if state.entity_repository is not None and podcast.slug and episode.slug:
            try:
                from ..services.role_linker import link_episode_roles, link_podcast_roles

                link_podcast_roles(
                    podcast_id=podcast.id,
                    podcast_slug=podcast.slug,
                    entity_repo=state.entity_repository,
                    path_manager=state.path_manager,
                )
                link_episode_roles(
                    episode_id=episode.id,
                    podcast_slug=podcast.slug,
                    episode_slug=episode.slug,
                    entity_repo=state.entity_repository,
                    path_manager=state.path_manager,
                )
            except Exception as exc:
                logger.warning(
                    "role_linking_failed",
                    episode_id=episode.id,
                    error=str(exc),
                    exc_info=True,
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

    # Load clean transcript via FileStorage (spec #35).
    clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
    clean_key = path_manager.to_relative(clean_path)

    # LLM errors are usually transient (rate limits, API issues)
    with _handler_error_context(f"summarizing {episode.title}"):
        try:
            transcript_text = config.file_storage.read_text(clean_key)
        except FileNotFoundError:
            raise FatalError(f"Clean transcript file not found: {clean_path}")

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
            language=podcast.language,
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

        # Summarize, then persist via FileStorage (spec #35) so the artefact
        # lands on the configured backend rather than always-local disk.
        # Spec #54 resolves timestamp citations against the annotated
        # transcript sidecar when present, writing the summary + citations
        # sidecar through the same helper the CLI uses.
        from .summary_artifacts import write_summary_manifest
        from .summary_citations import resolve_and_persist_summary_citations

        summary_text = summarizer.summarize(transcript_text, metadata=metadata)
        persisted_summary = resolve_and_persist_summary_citations(
            summary_markdown=summary_text,
            episode=episode,
            summary_path=output_path,
            path_manager=path_manager,
            file_storage=state.config.file_storage,
        )
        write_summary_manifest(
            state.config.file_storage,
            summary_key=path_manager.to_relative(output_path),
            summary_content=persisted_summary.markdown,
            canonical_language=podcast.language,
        )

        # Update episode state
        state.feed_manager.mark_episode_processed(
            str(podcast.rss_url),
            episode.external_id,
            raw_transcript_path=episode.raw_transcript_path,
            clean_transcript_path=episode.clean_transcript_path,
            summary_path=summary_db_path,
        )

        logger.info(f"Summarization completed for episode: {episode.title}")

        # Publish + fan out to follower inboxes. The conditional UPDATE in
        # ``mark_episode_published`` makes re-running summarize a no-op
        # (already-published episodes do not re-deliver). Fan-out runs
        # only on the actual NULL → set transition.
        if state.repository.mark_episode_published(episode.id):
            state.inbox_service.fanout_on_publish(episode.id, podcast.id)


def handle_entity_branch_placeholder(task: Task, state: "AppState") -> None:
    """Spec #28 §0.5 — shared no-op handler for entity stages still in Phase 0 mode.

    Phase 1 replaces these one-by-one with real handlers (GLiNER
    extractor, ReFinED resolver, chunk writer). Until
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


def handle_reindex(task: Task, state: "AppState") -> None:
    """Spec #28 §2.10 — embed and index the episode's transcript chunks.

    Reads ``clean_transcript_json_path``,
    runs the embedding model over each content segment, writes one
    ``chunks`` row per segment. The ``chunks_ai`` AFTER INSERT
    trigger fans out into ``chunks_vec`` (k-NN) and ``chunks_fts``
    (BM25), so this handler doesn't touch those mirror tables
    directly.

    Skips legacy episodes with no JSON sidecar (same rule as
    ``extract-entities``). Idempotent: re-running on an already-
    chunked episode at the same model is a no-op; pass ``--force``
    via ``thestill chunks backfill`` to re-embed.

    Failures here are non-user-facing — flipping the entity-extraction
    status, NOT ``failed_at_stage`` — because the user-visible pipeline
    is already done by this point.
    """
    from ..models.annotated_transcript import AnnotatedTranscript
    from ..repositories.factory import make_chunk_writer

    podcast, episode = _get_episode_or_fail(task, state)
    if not episode.clean_transcript_json_path:
        logger.info(
            "reindex_skipped_legacy",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
        )
        return

    sidecar = state.path_manager.clean_transcript_file(episode.clean_transcript_json_path)
    if not sidecar.exists():
        raise FatalError(f"AnnotatedTranscript sidecar not found: {sidecar} (episode {episode.id})")

    with _handler_error_context(f"chunking {episode.title}"):
        transcript = AnnotatedTranscript.model_validate_json(sidecar.read_text(encoding="utf-8"))
        # Spec #44 — backend-resolved: sqlite-vec locally, pgvector on Postgres.
        writer = make_chunk_writer(state.config, state.embedding_model)
        inserted = writer.write_episode(episode.id, transcript)
        logger.info(
            "reindex_completed",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
            chunks_inserted=inserted,
        )


def handle_rebuild_cooccurrences(task: Task, state: "AppState") -> None:
    """Spec #28 §1.7 — refresh ``entity_cooccurrences`` for the batch.

    Runs as the terminal stage of the entity branch. The previous design
    inlined this rebuild at the tail of every ``resolve-entities`` task,
    which serialised parallel workers on the SQLite writer lock — a
    corpus-wide self-join held inside a single write transaction is
    long enough to push concurrent writers past ``busy_timeout`` and
    produce transient ``database is locked`` failures.

    Splitting the rebuild into its own stage lets resolve workers commit
    their short transactions and move on; this handler then coalesces
    sibling pending rows via ``claim_pending_for_coalescing`` and
    issues one scoped rebuild over the union of episode_ids. Because
    the table is a corpus-wide aggregate (``episode_count`` distinct
    across episodes), a single rebuild covering the union is correctness-
    equivalent to N per-episode rebuilds.

    Held under ``_cooccurrence_rebuild_lock`` so two parallel workers
    in this stage don't both run the heavy SELECT against the same
    state — the second would block on the WAL writer for seconds and
    burn ``busy_timeout``.
    """
    repo = state.entity_repository

    with _handler_error_context(f"rebuilding cooccurrences for episode {task.episode_id}"):
        with _cooccurrence_rebuild_lock:
            coalesced = state.queue_manager.claim_pending_for_coalescing(TaskStage.REBUILD_COOCCURRENCES)
            # Dedup in case a peer worker queued a duplicate row for the
            # current episode between this task's claim-as-processing and
            # the coalescing UPDATE.
            episode_ids = sorted({task.episode_id, *coalesced})
            inserted = repo.rebuild_cooccurrences(episode_ids=episode_ids)
        logger.info(
            "cooccurrences_rebuild_completed",
            episode_id=task.episode_id,
            coalesced_episode_count=len(coalesced),
            rows=inserted,
        )


def handle_compute_related(task: Task, state: "AppState") -> None:
    """Spec #46 Tier 3 — refresh the "Related episodes" rail for the batch.

    Terminal entity-branch stage, modelled on ``handle_rebuild_cooccurrences``:
    coalesces sibling pending rows into one scoped update over the union of
    episode_ids, held under ``_related_compute_lock`` so two parallel workers
    don't both run the corpus-touching update. The rail is a derived cache,
    so a failure here is non-user-failing and never blocks the episode.

    The update reuses the persisted IDF (no corpus refit) and bounds work to
    the affected episodes plus their neighbours; below the candidate cap it
    is effectively a full rebuild (exact), above it a true incremental.
    """
    from ..repositories.factory import uses_postgres

    if uses_postgres(state.config):
        from ..search.pg_related_builder import update_related_for_episodes

        db_target = state.config.database_url
    else:
        from ..search.related_builder import update_related_for_episodes

        db_target = str(state.config.database_path)

    with _handler_error_context(f"computing related episodes for episode {task.episode_id}"):
        with _related_compute_lock:
            coalesced = state.queue_manager.claim_pending_for_coalescing(TaskStage.COMPUTE_RELATED)
            episode_ids = sorted({task.episode_id, *coalesced})
            result = update_related_for_episodes(
                db_target,
                embedding_model_name=state.embedding_model.model_name,
                episode_ids=episode_ids,
            )
        logger.info(
            "related_compute_completed",
            episode_id=task.episode_id,
            coalesced_episode_count=len(coalesced),
            pairs=result["pairs"],
        )


def handle_enrich_entities(task: Task, state: "AppState") -> None:
    """Spec #47 — fetch Wikidata/Wikipedia display data for the batch's entities.

    Terminal entity-branch stage, modelled on ``handle_compute_related``:
    coalesces sibling pending rows under ``_enrichment_lock`` and resolves the
    union of episode_ids to the entities that need (re)enriching via the
    repo's scoped ``entity_ids_needing_enrichment`` query. Each entity is then
    enriched sequentially — the network fetch happens OUTSIDE any DB
    transaction and each ``upsert_enrichment`` is its own short write, so the
    SQLite writer lock is never held across a 5s Wikimedia timeout.

    Why a stage at all (vs. inlining into resolve): enrichment is the only
    network-bound step in the branch and is pure display data. Running it LAST
    means REINDEX (search) and COMPUTE_RELATED (related rail) — the things
    users consume — finish first, and a Wikidata outage never delays them.

    Per-entity failures are swallowed (spec #42 FM-1) and left for retry: the
    enricher records ``FAILED`` + a ``retry_after``, and the scheduled
    ``thestill enrich-entities`` sweep re-selects them. This stage fires only
    for freshly-processed episodes, so the batch command remains the owner of
    transient-failure retries, 30-day staleness, and post-QID-correction
    re-enrichment — it is supplemented here, not replaced.

    Failure isolation: a hard error flips ``entity_extraction_status`` (via
    ``_NON_USER_FAILING_STAGES``), never ``failed_at_stage`` — the rail is a
    derived cache and the user-visible pipeline is long done by this point.
    """
    from ..models.enrichment import EnrichmentStatus
    from .entity_enricher import ENRICHMENT_SCHEMA_VERSION

    repo = state.entity_repository
    cfg = state.config

    with _handler_error_context(f"enriching entities for episode {task.episode_id}"):
        with _enrichment_lock:
            coalesced = state.queue_manager.claim_pending_for_coalescing(TaskStage.ENRICH_ENTITIES)
            episode_ids = sorted({task.episode_id, *coalesced})

            # Union the per-episode scoped selections. Each call already
            # applies the staleness gating (never-enriched / older schema /
            # failed-past-retry / >max_age), so we only touch entities that
            # genuinely need work.
            entity_ids: set[str] = set()
            for eid in episode_ids:
                entity_ids.update(
                    repo.entity_ids_needing_enrichment(
                        episode_id=eid,
                        schema_version=ENRICHMENT_SCHEMA_VERSION,
                        max_age_days=cfg.enrichment_max_age_days,
                    )
                )

            # Cap the burst; overflow is the scheduled sweep's job. Sorted so
            # the cap is deterministic across coalesced runs.
            ordered = sorted(entity_ids)
            capped = ordered[: cfg.enrichment_max_per_task]

            enricher = _get_or_create_entity_enricher(state)
            enriched = empty = failed = errored = 0
            for entity_id in capped:
                entity = repo.get_entity(entity_id)
                if entity is None or not entity.wikidata_qid:
                    continue
                try:
                    enrichment = enricher.enrich(entity)
                    repo.upsert_enrichment(enrichment)
                except Exception as exc:  # noqa: BLE001 — FM-1: one entity must not abort the batch
                    errored += 1
                    logger.warning("enrich_entity_failed", entity_id=entity_id, error=str(exc))
                    continue
                if EnrichmentStatus.FAILED in (enrichment.wikidata_status, enrichment.wikipedia_status):
                    failed += 1
                elif enrichment.has_content():
                    enriched += 1
                else:
                    empty += 1
                if cfg.enrichment_request_delay_sec > 0:
                    time.sleep(cfg.enrichment_request_delay_sec)

        logger.info(
            "entity_enrichment_completed",
            episode_id=task.episode_id,
            coalesced_episode_count=len(coalesced),
            candidates=len(entity_ids),
            attempted=len(capped),
            enriched=enriched,
            empty=empty,
            source_failed=failed,
            errored=errored,
            deferred_to_sweep=max(0, len(entity_ids) - len(capped)),
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
# Spec #28 §1.6 — serialise the inline alias-merge step across the
# resolve-entities worker pool. ``_merge_qid_duplicates_for`` reads a
# corpus-wide snapshot of duplicate QIDs then mutates entities; with
# ``RESOLVE_ENTITIES_PARALLEL_JOBS > 1`` two workers can each take a
# stale snapshot, both attempt to repoint+delete the same loser, and
# trigger a FOREIGN KEY constraint failure when the second worker's
# UPDATE references a row the first worker has already deleted.
# The lock is process-scope (matching the worker pool boundary), not
# DB-scope — multi-process deployments would need a different
# mechanism, but this codebase is single-process.
_qid_merge_lock = threading.Lock()
# Serialise ``rebuild_cooccurrences`` across the rebuild-cooccurrences
# worker pool. The rebuild's INSERT…SELECT does a corpus-wide self-join
# over ``entity_mentions`` under a single SQLite write transaction;
# letting two workers run it concurrently would queue them on the
# WAL writer lock, exceed ``busy_timeout``, and surface as transient
# ``database is locked`` retries. The handler also uses this lock to
# safely coalesce pending sibling tasks via
# ``QueueManager.claim_pending_for_coalescing``.
_cooccurrence_rebuild_lock = threading.Lock()

# Spec #46 Tier 3 — serialises the corpus-touching related-episodes update
# (and its coalescing claim) the same way, for the same reason.
_related_compute_lock = threading.Lock()

# Spec #47 — serialises the coalescing claim AND the Wikimedia request
# loop for the ENRICH_ENTITIES stage. Unlike the corpus locks above this
# is held mainly for politeness: it guarantees only one worker hits
# Wikidata/Wikipedia at a time, so the per-request delay actually paces
# total outbound traffic instead of N workers bursting in parallel.
_enrichment_lock = threading.Lock()


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

    with _handler_error_context(f"resolving entities for {episode.title}"):
        results: list = []
        coref_decisions: list = []
        forced_results: list = []

        if pending:
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
            #
            # Held under ``_qid_merge_lock`` so parallel resolve workers
            # can't both pick the same duplicate pair off a stale
            # ``find_duplicate_qid_pairs`` snapshot and race on the
            # repoint+delete sequence. Without this, the second worker
            # FK-fails when its UPDATE/INSERT references an entity row
            # the first worker has already deleted.
            with _qid_merge_lock:
                merged = _merge_qid_duplicates_for(repo, touched_entity_ids)
            if merged:
                logger.info("alias_merge_inline", merged_pairs=merged, episode_id=episode.id)

        # Spec §1.7 — cooccurrences are rebuilt by the dedicated
        # ``rebuild-cooccurrences`` stage at the tail of the entity
        # branch. Running the rebuild here would hold a corpus-wide
        # write transaction inside every parallel resolve worker,
        # serialising them on the SQLite writer lock and producing
        # ``database is locked`` retries under load. The trailing
        # stage coalesces sibling episodes' rebuilds into one call
        # under a process-scope lock.
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
            from .wikidata_client import WikidataClient

            state.entity_resolver = EntityResolver(wikidata_client=WikidataClient())
    return state.entity_resolver


def _get_or_create_entity_enricher(state: "AppState"):
    """Lazy-init the process-scope ``EntityEnricher``.

    Unlike the resolver, the enricher is cheap to build (two HTTP clients +
    in-process LRU/label caches). We still cache it on ``AppState`` so the
    label cache survives across coalesced ENRICH_ENTITIES tasks rather than
    being thrown away each run. Built under ``_enrichment_lock`` (already
    held by the only caller) so two workers can't double-init.
    """
    if state.entity_enricher is None:
        from .entity_enricher import EntityEnricher
        from .wikidata_client import WikidataClient
        from .wikipedia_client import WikipediaClient

        cfg = state.config
        state.entity_enricher = EntityEnricher(
            wikidata_client=WikidataClient(user_agent=cfg.enrichment_user_agent),
            wikipedia_client=WikipediaClient(user_agent=cfg.enrichment_user_agent),
            find_entity_by_qid=state.entity_repository.find_entity_by_qid,
            language=cfg.enrichment_wikipedia_lang,
        )
    return state.entity_enricher


def handle_refresh_feed(task: Task, state: "AppState") -> None:
    """Spec #48 — refresh ONE feed as a queued, podcast-scoped task.

    Wraps the existing ``_refresh_single_podcast`` unit of work so the queued
    path discovers exactly what the inline batch would. Key contracts:

    - **Raise on ``had_error``** — ``_refresh_single_podcast`` returns normally
      with ``had_error=True`` (a batch contract). The task contract must
      surface that as a raised error so the worker retries / DLQs it; otherwise
      a failed fetch becomes a silently-completed task. FM-2: no cache headers
      are persisted on the failure path, so the next fetch re-validates.
    - **Per-feed persist** in its own short transaction (incremental
      visibility), mirroring the inline ``_record_outcome`` semantics.
    - **Reconcile** episode ids after ``INSERT OR IGNORE`` before fan-out.
    - **Fresh priority** on the enqueued DOWNLOADs so new episodes jump backfill.
    """
    from ..utils.config import (
        get_default_refresh_interval_seconds,
        get_refresh_max_interval_seconds,
        get_refresh_min_interval_seconds,
    )
    from .media_source import RSSMediaSource
    from .refresh_failure import RefreshPolicySettings, error_class_for_failure

    podcast_id = task.podcast_id
    if not podcast_id:
        raise FatalError("REFRESH_FEED task has no podcast_id")

    repo = state.repository
    fm = state.feed_manager

    loaded = repo.get_podcast_for_refresh(podcast_id)
    if loaded is None:
        raise FatalError(f"Podcast not found for refresh: {podcast_id}")
    podcast, known_external_ids = loaded

    max_eps = getattr(state.config, "max_episodes_per_podcast", None)

    # 1-2. Fetch (network outside any txn) and surface errors.
    result = fm._refresh_single_podcast(podcast, max_eps, known_external_ids)
    podcast = result.podcast
    new_eps = result.new_episodes
    hit = result.conditional_hit
    source = result.source
    headers_rotated = result.headers_rotated
    image_rows = result.image_rows
    audio_rows = result.audio_rows
    if result.failure is not None:
        # Spec #60 — ONE authoritative policy-applying write per attempt.
        # The repository reads the current AIMD/streak state and applies
        # decide_refresh_action atomically; the worker's exhaustion-time
        # path only records a fallback when this write never happened
        # (``refresh_failure_recorded`` below).
        failure = result.failure
        settings = RefreshPolicySettings.from_config()
        recorded = False
        try:
            decision = repo.record_refresh_failure(podcast_id, failure, settings)
            recorded = True
            logger.info(
                "refresh_failure_recorded",
                podcast_id=podcast_id,
                failure_kind=failure.kind.value,
                http_status=failure.http_status,
                action=decision.action.value,
                disabled_reason=decision.disabled_reason,
            )
        except Exception:
            logger.warning("refresh_error_stamp_failed", podcast_id=podcast_id, exc_info=True)

        error_class = error_class_for_failure(failure)
        msg = f"Feed refresh failed for {podcast.title} ({podcast_id}): {failure.kind.value}"
        exc_type = FatalError if error_class == "fatal" else TransientError
        exc = exc_type(
            msg,
            error_class=error_class,
            refresh_failure_kind=failure.kind.value,
            http_status=failure.http_status,
        )
        # Typed marker for the worker's idempotent fallback: True means the
        # podcast row already carries this attempt's classified failure.
        exc.refresh_failure_recorded = recorded
        raise exc

    # 3. Per-feed persist — only on success. A plain 304 (no rotated headers)
    #    persists nothing; otherwise persist the podcast (+ new episodes).
    persist_podcast = (not hit) or headers_rotated
    changed = [podcast] if persist_podcast else []
    if changed or new_eps or image_rows or audio_rows:
        repo.save_refresh_batch(changed, new_eps, image_rows, audio_rows)

    # 4. Auto-enqueue the first pipeline stage for every DISCOVERED-but-unqueued
    #    episode of this feed. Shared with the inline refresh path
    #    (``RefreshService.refresh``) so every refresh trigger behaves the same.
    #    Drives off DB state (idempotent + crash-recovery) and applies the
    #    initial-backfill cap for brand-new podcasts. See the helper for detail.
    enqueued = state.queue_manager.enqueue_discovered_episodes(
        podcast_id=podcast_id,
        repository=repo,
        config=state.config,
        initiated_by="refresh-feed",
    )

    # 5. Best-effort transcript-link extraction (outside the txn, unchanged).
    if new_eps and isinstance(source, RSSMediaSource):
        try:
            fm._save_transcript_links_for_episodes(podcast, new_eps, source)
        except Exception:
            logger.warning("transcript_link_extraction_failed", podcast_id=podcast_id, exc_info=True)

    # 6. Record success + recompute adaptive (AIMD) cadence.
    repo.record_refresh_success(
        podcast_id,
        found_new=bool(new_eps),
        min_interval=get_refresh_min_interval_seconds(),
        max_interval=get_refresh_max_interval_seconds(),
        default_interval=get_default_refresh_interval_seconds(),
    )
    logger.info(
        "refresh_feed_complete",
        podcast_id=podcast_id,
        new_episodes=len(new_eps),
        tasks_enqueued=enqueued,
        conditional_get_hit=hit,
    )


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
        TaskStage.REINDEX: lambda task, cb=None: handle_reindex(task, state),
        TaskStage.REBUILD_COOCCURRENCES: lambda task, cb=None: handle_rebuild_cooccurrences(task, state),
        TaskStage.COMPUTE_RELATED: lambda task, cb=None: handle_compute_related(task, state),
        TaskStage.ENRICH_ENTITIES: lambda task, cb=None: handle_enrich_entities(task, state),
        TaskStage.REFRESH_FEED: lambda task, cb=None: handle_refresh_feed(task, state),
    }
