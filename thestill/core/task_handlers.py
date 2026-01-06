# Copyright 2025 thestill.me
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
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict

from .audio_downloader import AudioDownloader
from .audio_preprocessor import AudioPreprocessor
from .progress import ProgressCallback, ProgressUpdate, TranscriptionStage
from .queue_manager import Task, TaskStage

if TYPE_CHECKING:
    from ..web.dependencies import AppState

logger = logging.getLogger(__name__)


def handle_download(task: Task, state: "AppState") -> None:
    """
    Download audio for an episode.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        RuntimeError: If download fails
    """
    logger.info(f"Processing download task for episode {task.episode_id}")

    # Get episode and podcast (get_episode returns tuple of (Podcast, Episode))
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise RuntimeError(f"Episode not found: {task.episode_id}")

    podcast, episode = result

    # Create downloader and download
    downloader = AudioDownloader(str(state.path_manager.original_audio_dir()))
    audio_path = downloader.download_episode(episode, podcast)

    if not audio_path:
        raise RuntimeError(f"Download failed for episode: {episode.title}")

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
        RuntimeError: If downsampling fails
    """
    logger.info(f"Processing downsample task for episode {task.episode_id}")

    # Get episode and podcast (get_episode returns tuple of (Podcast, Episode))
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise RuntimeError(f"Episode not found: {task.episode_id}")

    podcast, episode = result

    if not episode.audio_path:
        raise RuntimeError(f"No audio path for episode: {task.episode_id}")

    # Verify original audio exists
    original_audio_file = state.path_manager.original_audio_file(episode.audio_path)
    state.path_manager.require_file_exists(original_audio_file, "Original audio file not found")

    # Determine output directory
    audio_path_obj = Path(episode.audio_path)
    if len(audio_path_obj.parts) > 1:
        podcast_subdir = audio_path_obj.parent
        output_dir = state.path_manager.downsampled_audio_dir() / podcast_subdir
    else:
        output_dir = state.path_manager.downsampled_audio_dir() / podcast.slug

    output_dir.mkdir(parents=True, exist_ok=True)

    # Downsample
    preprocessor = AudioPreprocessor()
    downsampled_path = preprocessor.downsample_audio(str(original_audio_file), str(output_dir))

    if not downsampled_path:
        raise RuntimeError(f"Downsampling failed for episode: {episode.title}")

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
        RuntimeError: If transcription fails
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

    # Get episode and podcast (get_episode returns tuple of (Podcast, Episode))
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise RuntimeError(f"Episode not found: {task.episode_id}")

    podcast, episode = result

    if not episode.downsampled_audio_path:
        raise RuntimeError(f"No downsampled audio for episode: {task.episode_id}")

    config = state.config

    # Verify audio file exists
    audio_file = config.path_manager.downsampled_audio_file(episode.downsampled_audio_path)
    config.path_manager.require_file_exists(audio_file, "Downsampled audio file not found")

    # Create transcriber based on config (with progress callback if available)
    transcriber = _create_transcriber(config, config.path_manager, progress_callback)

    # Determine output path
    path_parts = Path(episode.downsampled_audio_path).parts
    if len(path_parts) >= 2:
        podcast_subdir = path_parts[0]
    else:
        podcast_subdir = podcast.slug

    transcript_dir = config.path_manager.raw_transcripts_dir() / podcast_subdir
    transcript_dir.mkdir(parents=True, exist_ok=True)

    transcript_filename = f"{audio_file.stem}_transcript.json"
    output = str(transcript_dir / transcript_filename)
    output_db_path = f"{podcast_subdir}/{transcript_filename}"

    # Transcribe
    transcript_data = transcriber.transcribe_audio(
        str(audio_file),
        output,
        episode_id=episode.id,
        podcast_slug=podcast.slug,
        episode_slug=episode.slug,
    )

    if not transcript_data:
        raise RuntimeError(f"Transcription failed for episode: {episode.title}")

    # Update episode state
    state.feed_manager.mark_episode_processed(
        str(podcast.rss_url),
        episode.external_id,
        raw_transcript_path=output_db_path,
        clean_transcript_path="",  # Clear - needs re-cleaning
        summary_path="",  # Clear - needs re-summarizing
    )

    logger.info(f"Transcription completed for episode: {episode.title}")


def handle_clean(task: Task, state: "AppState") -> None:
    """
    Clean transcript using LLM.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        RuntimeError: If cleaning fails
    """
    logger.info(f"Processing clean task for episode {task.episode_id}")

    # Get episode and podcast (get_episode returns tuple of (Podcast, Episode))
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise RuntimeError(f"Episode not found: {task.episode_id}")

    podcast, episode = result

    if not episode.raw_transcript_path:
        raise RuntimeError(f"No raw transcript for episode: {task.episode_id}")

    config = state.config
    path_manager = state.path_manager

    # Load transcript
    transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
    if not transcript_path.exists():
        raise RuntimeError(f"Transcript file not found: {transcript_path}")

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_data = json.load(f)

    # Create LLM provider
    from .llm_provider import create_llm_provider
    from .transcript_cleaning_processor import TranscriptCleaningProcessor

    llm_provider = create_llm_provider(
        provider_type=config.llm_provider,
        openai_api_key=config.openai_api_key,
        openai_model=config.openai_model,
        openai_reasoning_effort=config.openai_reasoning_effort,
        ollama_base_url=config.ollama_base_url,
        ollama_model=config.ollama_model,
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
        gemini_thinking_level=config.gemini_thinking_level,
        anthropic_api_key=config.anthropic_api_key,
        anthropic_model=config.anthropic_model,
    )

    cleaning_processor = TranscriptCleaningProcessor(llm_provider)

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
    result = cleaning_processor.clean_transcript(
        transcript_data=transcript_data,
        podcast_title=podcast.title,
        podcast_description=podcast.description,
        episode_title=episode.title,
        episode_description=episode.description,
        podcast_slug=podcast.slug,
        episode_slug=episode.slug,
        output_path=str(cleaned_path),
        path_manager=path_manager,
    )

    if not result:
        raise RuntimeError(f"Transcript cleaning failed for episode: {episode.title}")

    # Update episode state
    state.feed_manager.mark_episode_processed(
        str(podcast.rss_url),
        episode.external_id,
        raw_transcript_path=episode.raw_transcript_path,
        clean_transcript_path=clean_transcript_db_path,
    )

    logger.info(f"Transcript cleaning completed for episode: {episode.title}")


def handle_summarize(task: Task, state: "AppState") -> None:
    """
    Summarize transcript using LLM.

    Args:
        task: Task containing episode_id
        state: Application state with services

    Raises:
        RuntimeError: If summarization fails
    """
    logger.info(f"Processing summarize task for episode {task.episode_id}")

    # Get episode and podcast (get_episode returns tuple of (Podcast, Episode))
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise RuntimeError(f"Episode not found: {task.episode_id}")

    podcast, episode = result

    if not episode.clean_transcript_path:
        raise RuntimeError(f"No clean transcript for episode: {task.episode_id}")

    config = state.config
    path_manager = state.path_manager

    # Load clean transcript
    clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
    if not clean_path.exists():
        raise RuntimeError(f"Clean transcript file not found: {clean_path}")

    with open(clean_path, "r", encoding="utf-8") as f:
        transcript_text = f.read()

    # Create LLM provider and summarizer
    from .llm_provider import create_llm_provider
    from .post_processor import TranscriptSummarizer

    llm_provider = create_llm_provider(
        provider_type=config.llm_provider,
        openai_api_key=config.openai_api_key,
        openai_model=config.openai_model,
        openai_reasoning_effort=config.openai_reasoning_effort,
        ollama_base_url=config.ollama_base_url,
        ollama_model=config.ollama_model,
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
        gemini_thinking_level=config.gemini_thinking_level,
        anthropic_api_key=config.anthropic_api_key,
        anthropic_model=config.anthropic_model,
    )

    summarizer = TranscriptSummarizer(llm_provider)

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
    summarizer.summarize(transcript_text, output_path)

    # Update episode state
    state.feed_manager.mark_episode_processed(
        str(podcast.rss_url),
        episode.external_id,
        raw_transcript_path=episode.raw_transcript_path,
        clean_transcript_path=episode.clean_transcript_path,
        summary_path=summary_db_path,
    )

    logger.info(f"Summarization completed for episode: {episode.title}")


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
    if config.transcription_provider.lower() == "google":
        from .google_transcriber import GoogleCloudTranscriber

        return GoogleCloudTranscriber(
            credentials_path=config.google_app_credentials or None,
            project_id=config.google_cloud_project_id or None,
            storage_bucket=config.google_storage_bucket or None,
            enable_diarization=config.enable_diarization,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            parallel_chunks=config.max_workers,
            path_manager=path_manager,
        )
    elif config.transcription_provider.lower() == "elevenlabs":
        from .elevenlabs_transcriber import ElevenLabsTranscriber

        return ElevenLabsTranscriber(
            api_key=config.elevenlabs_api_key,
            model=config.elevenlabs_model,
            enable_diarization=config.enable_diarization,
            num_speakers=config.max_speakers,
            path_manager=path_manager,
            use_async=False,  # Synchronous mode for background tasks
        )
    elif config.enable_diarization:
        from .whisper_transcriber import WhisperXTranscriber

        return WhisperXTranscriber(
            model_name=config.whisper_model,
            device=config.whisper_device,
            enable_diarization=True,
            hf_token=config.huggingface_token,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            diarization_model=config.diarization_model,
            progress_callback=progress_callback,
        )
    else:
        from .whisper_transcriber import WhisperTranscriber

        return WhisperTranscriber(config.whisper_model, config.whisper_device)


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
