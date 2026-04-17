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
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Generator, Optional, Tuple

from structlog import get_logger

from thestill.utils.exceptions import FatalError, ThestillError, TransientError

from ..models.podcast import Episode, Podcast
from ..models.transcription import TranscribeOptions
from ..utils.console import ConsoleOutput
from .audio_downloader import AudioDownloader
from .audio_preprocessor import AudioPreprocessor
from .error_classifier import classify_and_raise
from .progress import ProgressCallback, ProgressUpdate, TranscriptionStage
from .queue_manager import Task, TaskStage

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
        downloader = AudioDownloader(str(state.path_manager.original_audio_dir()))
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

            downloader = AudioDownloader(str(state.path_manager.original_audio_dir()))
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
        transcriber = _create_transcriber(config, config.path_manager, progress_callback)
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


def validate_transcription_provider(config) -> None:
    """
    Fail fast if the configured transcription provider cannot be used.

    This check runs at startup so misconfigured deployments surface the
    problem before any episode is processed. It verifies two things per
    provider:

    1. The Python module(s) backing the provider are importable. This
       catches the slim-Docker-image case where `TRANSCRIPTION_PROVIDER`
       points at a local-transcription provider (whisper / whisperx /
       parakeet) but the `local-transcription` optional-dependencies
       extra is not installed.
    2. Required runtime config is present. This catches the "module is
       installed but the user forgot to set DALSTON_BASE_URL /
       ELEVENLABS_API_KEY / GOOGLE_APP_CREDENTIALS" case that otherwise
       only surfaces at first-transcribe.

    The check uses importlib.util.find_spec rather than a real import
    so it is cheap, has no side effects, and does not defeat Phase A's
    lazy-import refactor. It never connects to external services.

    Args:
        config: Application configuration (thestill.utils.config.Config).

    Raises:
        ThestillError: With a specific remediation message when the
            configured provider cannot be used.
    """
    import importlib.util

    provider = (config.transcription_provider or "").lower()

    def _require_modules(modules: list[str], extra: str) -> None:
        missing = [m for m in modules if importlib.util.find_spec(m) is None]
        if missing:
            raise ThestillError(
                f"TRANSCRIPTION_PROVIDER={provider!r} requires {', '.join(missing)} "
                f"which is not installed. Install with: pip install '.[{extra}]' "
                f"— or switch TRANSCRIPTION_PROVIDER to a cloud provider "
                f"(dalston, google, elevenlabs).",
                provider=provider,
                missing_modules=missing,
                remediation_extra=extra,
            )

    def _require_config(field: str, env_var: str, provider_label: str) -> None:
        if not getattr(config, field, None):
            raise ThestillError(
                f"TRANSCRIPTION_PROVIDER={provider!r} requires {env_var} to be set. "
                f"Add {env_var}=... to your .env file.",
                provider=provider,
                missing_config=env_var,
                provider_label=provider_label,
            )

    if provider == "dalston":
        _require_config("dalston_base_url", "DALSTON_BASE_URL", "Dalston")
    elif provider == "google":
        _require_config("google_app_credentials", "GOOGLE_APP_CREDENTIALS", "Google Cloud Speech")
        _require_config("google_cloud_project_id", "GOOGLE_CLOUD_PROJECT_ID", "Google Cloud Speech")
    elif provider == "elevenlabs":
        _require_config("elevenlabs_api_key", "ELEVENLABS_API_KEY", "ElevenLabs")
    elif provider == "parakeet":
        _require_modules(["torch", "transformers", "librosa"], "local-transcription")
    elif provider in ("whisper", "whisperx", ""):
        # Empty string is the fallback branch in _create_transcriber, which
        # uses WhisperTranscriber / WhisperXTranscriber.
        required = ["torch", "whisper"]
        if config.enable_diarization:
            required.append("whisperx")
        _require_modules(required, "local-transcription")
    else:
        raise ThestillError(
            f"Unknown TRANSCRIPTION_PROVIDER={provider!r}. "
            f"Valid values: dalston, google, elevenlabs, whisper, whisperx, parakeet.",
            provider=provider,
        )

    logger.info(
        "transcription_provider_validated",
        provider=provider,
        diarization=config.enable_diarization,
    )


def _create_transcriber(
    config,
    path_manager,
    progress_callback: ProgressCallback | None = None,
):
    """
    Create transcriber based on configuration.

    Args:
        config: Application configuration
        path_manager: Path manager for file access
        progress_callback: Optional callback for progress reporting

    Returns:
        Configured transcriber instance
    """
    validate_transcription_provider(config)

    if config.transcription_provider.lower() == "google":
        from .google_transcriber import GoogleCloudTranscriber

        # Use quiet console to avoid broken pipe errors in web worker context
        return GoogleCloudTranscriber(
            credentials_path=config.google_app_credentials or None,
            project_id=config.google_cloud_project_id or None,
            storage_bucket=config.google_storage_bucket or None,
            enable_diarization=config.enable_diarization,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            parallel_chunks=config.max_workers,
            path_manager=path_manager,
            console=ConsoleOutput(quiet=True),
        )
    elif config.transcription_provider.lower() == "elevenlabs":
        from .elevenlabs_transcriber import ElevenLabsTranscriber

        return ElevenLabsTranscriber(
            api_key=config.elevenlabs_api_key,
            base_url=config.elevenlabs_base_url or None,
            model=config.elevenlabs_model,
            enable_diarization=config.enable_diarization,
            num_speakers=config.max_speakers,
            path_manager=path_manager,
            use_async=False,  # Synchronous mode for background tasks
        )
    elif config.transcription_provider.lower() == "dalston":
        from .dalston_transcriber import DalstonTranscriber

        return DalstonTranscriber(
            base_url=config.dalston_base_url or None,
            api_key=config.dalston_api_key or None,
            model=config.dalston_model or None,
            enable_diarization=config.enable_diarization,
            num_speakers=config.max_speakers,
            path_manager=path_manager,
        )
    elif config.enable_diarization:
        from .whisper_transcriber import WhisperXTranscriber

        # Use quiet console to avoid broken pipe errors in web worker context
        # (stdout may not be connected when running as background task)
        return WhisperXTranscriber(
            model_name=config.whisper_model,
            device=config.whisper_device,
            enable_diarization=True,
            hf_token=config.huggingface_token,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            diarization_model=config.diarization_model,
            progress_callback=progress_callback,
            console=ConsoleOutput(quiet=True),
        )
    else:
        from .whisper_transcriber import WhisperTranscriber

        # Use quiet console to avoid broken pipe errors in web worker context
        return WhisperTranscriber(
            config.whisper_model,
            config.whisper_device,
            console=ConsoleOutput(quiet=True),
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
    }
