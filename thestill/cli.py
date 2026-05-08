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

import functools
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import click

# Import thestill modules using relative imports
# This module can be executed in two ways:
# 1. Package mode (recommended): `thestill` command (defined in pyproject.toml entry point)
# 2. Module mode (development): `python -m thestill.cli` (uses __main__ guard at bottom)
from .core.audio_downloader import AudioDownloader
from .core.audio_preprocessor import AudioPreprocessor
from .core.evaluator import PostProcessorEvaluator, TranscriptEvaluator, print_evaluation_summary
from .core.external_transcript_downloader import ExternalTranscriptDownloader
from .core.feed_manager import PodcastFeedManager
from .core.google_transcriber import GoogleCloudTranscriber
from .core.llm_provider import create_llm_provider, create_llm_provider_from_config
from .core.post_processor import EpisodeMetadata, TranscriptSummarizer
from .logging import configure_structlog
from .models.digest import Digest, DigestStatus
from .models.podcast import EpisodeState
from .models.transcription import TranscribeOptions
from .repositories.sqlite_digest_repository import SqliteDigestRepository
from .repositories.sqlite_inbox_repository import SqliteInboxRepository
from .repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from .repositories.sqlite_podcast_repository import SqlitePodcastRepository
from .repositories.sqlite_user_repository import SqliteUserRepository
from .services import (
    BatchQueueService,
    DigestEpisodeSelector,
    DigestGenerator,
    DigestSelectionCriteria,
    PodcastService,
    RefreshService,
    StatsService,
)
from .services.auth_service import AuthService
from .services.inbox_service import InboxService
from .utils.cli_formatter import CLIFormatter
from .utils.cli_logging import log_command
from .utils.config import load_config
from .utils.console import ConsoleOutput
from .utils.duration import format_duration, format_speed_stats, get_audio_duration, parse_time_window
from .utils.logger import setup_logger
from .utils.path_manager import PathManager


class CLIContext:
    """Container for CLI dependency injection with type safety."""

    def __init__(
        self,
        config,
        path_manager: PathManager,
        repository,
        podcast_service,
        stats_service,
        feed_manager,
        audio_downloader,
        audio_preprocessor,
        external_transcript_downloader,
        console: ConsoleOutput,
        auth_service: AuthService,
        digest_repository: SqliteDigestRepository,
        follower_repository: SqlitePodcastFollowerRepository,
        inbox_repository: SqliteInboxRepository,
        inbox_service: InboxService,
        entity_repository=None,
        search_backend=None,
        embedding_model=None,
    ):
        self.config = config
        self.path_manager = path_manager
        self.repository = repository
        self.podcast_service = podcast_service
        self.stats_service = stats_service
        self.feed_manager = feed_manager
        self.audio_downloader = audio_downloader
        self.audio_preprocessor = audio_preprocessor
        self.external_transcript_downloader = external_transcript_downloader
        self.console = console
        self.auth_service = auth_service
        self.entity_repository = entity_repository
        self.follower_repository = follower_repository
        self.inbox_repository = inbox_repository
        self.inbox_service = inbox_service
        # Spec #28 §1.5 — lazy ReFinED resolver, constructed on first
        # use by ``thestill resolve-entities`` and
        # ``rebuild-cooccurrences``; CLI invocations that don't touch
        # the entity branch pay nothing.
        self.entity_resolver = None
        # Spec #28 §2.10 — sqlite-vec corpus search. EmbeddingModel
        # wraps sentence-transformers; the underlying weights only
        # load on first ``encode_one`` call.
        self.search_backend = search_backend
        self.embedding_model = embedding_model
        self.digest_repository = digest_repository


def require_config(f):
    """
    Decorator to ensure CLIContext is loaded before command runs.

    Displays a user-friendly error message if configuration failed to load.
    Should be applied after @click.pass_context.

    Example:
        @main.command()
        @click.pass_context
        @require_config
        def my_command(ctx):
            # ctx.obj is guaranteed to be CLIContext here
            pass
    """

    @functools.wraps(f)
    def wrapper(ctx, *args, **kwargs):
        if ctx.obj is None:
            click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
            ctx.exit(1)
        return f(ctx, *args, **kwargs)

    return wrapper


@click.group()
@click.option("--config", "-c", help="Path to config file")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-error output")
@click.pass_context
def main(ctx, config, quiet):
    """Thestill - Automated podcast transcription and summarization"""
    # Load .env file first so LOG_* environment variables are available
    from dotenv import load_dotenv

    load_dotenv()

    # Configure structlog for backend logging (logs go to stderr)
    configure_structlog()

    # Keep legacy logger setup for any remaining code that uses it
    setup_logger("thestill", log_level="INFO", console_output=True)

    # Create console output instance (respects --quiet flag)
    console = ConsoleOutput(quiet=quiet)

    try:
        config_obj = load_config(config)
        if not quiet:
            click.echo("✓ Configuration loaded successfully")

        # Initialize all shared services once (dependency injection)
        storage_path = config_obj.storage_path  # Path object
        path_manager = PathManager(str(storage_path))
        repository = SqlitePodcastRepository(db_path=config_obj.database_path)
        podcast_service = PodcastService(storage_path, repository, path_manager)
        stats_service = StatsService(storage_path, repository, path_manager)
        feed_manager = PodcastFeedManager(
            repository,
            path_manager,
            max_workers=config_obj.refresh_max_workers,
            max_per_host=config_obj.refresh_max_per_host,
        )
        audio_downloader = AudioDownloader(
            str(path_manager.original_audio_dir()),
            max_bytes=config_obj.max_audio_bytes,
        )
        audio_preprocessor = AudioPreprocessor(console=console)
        external_transcript_downloader = ExternalTranscriptDownloader(repository, path_manager)

        # Initialize auth service for default user support
        user_repository = SqliteUserRepository(db_path=config_obj.database_path)
        auth_service = AuthService(config_obj, user_repository)

        # Initialize digest repository for digest persistence
        digest_repository = SqliteDigestRepository(str(config_obj.database_path))

        # Per-user inbox plumbing: the backfill / follow-seed CLI paths
        # need both the repository and the service.
        follower_repository = SqlitePodcastFollowerRepository(db_path=config_obj.database_path)
        inbox_repository = SqliteInboxRepository(db_path=config_obj.database_path)
        inbox_service = InboxService.from_config(config_obj, inbox_repository, follower_repository)

        # Spec #28 — entity-layer repository (always-on; the schema
        # is created by SqlitePodcastRepository's migration block).
        from .core.embedding_model import EmbeddingModel
        from .repositories.sqlite_entity_repository import SqliteEntityRepository
        from .search.sqlite_vec_client import SqliteVecBackend

        entity_repository = SqliteEntityRepository(db_path=config_obj.database_path)
        embedding_model = EmbeddingModel(config_obj.embedding_model)
        search_backend = SqliteVecBackend(
            db_path=config_obj.database_path,
            embedding_model=embedding_model,
        )

        # Store all services in typed context object
        ctx.obj = CLIContext(
            config=config_obj,
            path_manager=path_manager,
            repository=repository,
            podcast_service=podcast_service,
            stats_service=stats_service,
            feed_manager=feed_manager,
            audio_downloader=audio_downloader,
            audio_preprocessor=audio_preprocessor,
            external_transcript_downloader=external_transcript_downloader,
            console=console,
            auth_service=auth_service,
            digest_repository=digest_repository,
            follower_repository=follower_repository,
            inbox_repository=inbox_repository,
            inbox_service=inbox_service,
            entity_repository=entity_repository,
            search_backend=search_backend,
            embedding_model=embedding_model,
        )

    except Exception as e:
        click.echo(f"❌ Configuration error: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument("rss_url")
@click.pass_context
@require_config
@log_command
def add(ctx, rss_url):
    """Add a podcast RSS feed"""
    podcast = ctx.obj.podcast_service.add_podcast(rss_url)
    if podcast:
        click.echo(f"✓ Podcast added: {podcast.title}")
    else:
        click.echo("❌ Failed to add podcast or podcast already exists", err=True)


@main.command()
@click.argument("podcast_id")
@click.pass_context
@require_config
@log_command
def remove(ctx, podcast_id):
    """Remove a podcast by RSS URL or index number"""
    if ctx.obj.podcast_service.remove_podcast(podcast_id):
        click.echo("✓ Podcast removed")
    else:
        click.echo("❌ Podcast not found", err=True)


@main.command()
@click.pass_context
@require_config
@log_command
def list(ctx):
    """List all tracked podcasts"""

    podcasts = ctx.obj.podcast_service.get_podcasts()
    output = CLIFormatter.format_podcast_list(podcasts)
    click.echo(output)


@main.command()
@click.option("--podcast-id", help="Refresh specific podcast (index or RSS URL)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to discover per podcast")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be discovered without updating feeds.json")
@click.pass_context
@require_config
@log_command
def refresh(ctx, podcast_id, max_episodes, dry_run):
    """Refresh podcast feeds and discover new episodes (step 1)"""
    # Use shared services from context
    config = ctx.obj.config
    refresh_service = RefreshService(ctx.obj.feed_manager, ctx.obj.podcast_service)

    # Use CLI option if provided, otherwise fall back to config
    max_episodes_limit = max_episodes if max_episodes else config.max_episodes_per_podcast

    # Check for new episodes
    click.echo("🔍 Checking for new episodes...")
    if max_episodes_limit:
        click.echo(f"   (Limiting to {max_episodes_limit} episodes per podcast)")

    try:
        result = refresh_service.refresh(
            podcast_id=podcast_id,
            max_episodes=max_episodes,
            max_episodes_per_podcast=max_episodes_limit,
            dry_run=dry_run,
        )
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        ctx.exit(1)

    if result.total_episodes == 0:
        if result.podcast_filter_applied:
            click.echo(f"✓ No new episodes found for podcast: {result.podcast_filter_applied}")
        else:
            click.echo("✓ No new episodes found")
        return

    click.echo(f"📡 Found {result.total_episodes} new episode(s)")

    # Display episode names grouped by podcast
    for podcast, episodes in result.episodes_by_podcast:
        click.echo(f"\n📻 {podcast.title}")
        for episode in episodes:
            click.echo(f"  • {episode.title}")

    if dry_run:
        click.echo("\n(Run without --dry-run to update feeds.json)")
        return

    click.echo(f"\n✅ Refresh complete! Discovered {result.total_episodes} new episode(s)")
    click.echo("💡 Next step: Run 'thestill download' to download audio files")


@main.command()
@click.option("--podcast-id", help="Download from specific podcast (index or RSS URL)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to download per podcast")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be downloaded without downloading")
@click.pass_context
@require_config
@log_command
def download(ctx, podcast_id, max_episodes, dry_run):
    """Download audio files for episodes that need downloading (step 2)"""
    # Use shared services from context
    config = ctx.obj.config
    feed_manager = ctx.obj.feed_manager
    downloader = ctx.obj.audio_downloader
    podcast_service = ctx.obj.podcast_service
    path_manager = ctx.obj.path_manager

    # Get episodes that need downloading
    click.echo("🔍 Looking for episodes to download...")
    episodes_to_download = feed_manager.get_episodes_to_download(str(config.storage_path))

    if not episodes_to_download:
        click.echo("✓ No episodes found that need downloading")
        click.echo("💡 Run 'thestill refresh' first to discover new episodes")
        return

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)

        episodes_to_download = [(p, eps) for p, eps in episodes_to_download if str(p.rss_url) == str(podcast.rss_url)]

        if not episodes_to_download:
            click.echo(f"✓ No episodes need downloading for podcast: {podcast.title}")
            return

    # Apply max_episodes limit
    if max_episodes:
        total = 0
        filtered = []
        for podcast, episodes in episodes_to_download:
            remaining = max_episodes - total
            if remaining <= 0:
                break
            filtered.append((podcast, episodes[:remaining]))
            total += len(episodes[:remaining])
        episodes_to_download = filtered

    # Count total episodes
    total_episodes = sum(len(eps) for _, eps in episodes_to_download)
    click.echo(f"📥 Found {total_episodes} episode(s) to download")

    if dry_run:
        for podcast, episodes in episodes_to_download:
            click.echo(f"\n📻 {podcast.title}")
            for episode in episodes:
                click.echo(f"  • {episode.title}")
        click.echo("\n(Run without --dry-run to actually download)")
        return

    # Download episodes
    downloaded_count = 0
    start_time = time.time()

    # Flatten episodes for progress bar
    all_episodes = []
    for podcast, episodes in episodes_to_download:
        for episode in episodes:
            all_episodes.append((podcast, episode))

    # Progress bar wrapper
    with click.progressbar(
        all_episodes,
        label="Downloading",
        show_pos=True,  # Show "X/Y" counter
        show_eta=True,  # Show estimated time
        file=sys.stderr,  # Use stderr (consistent with logging)
        item_show_func=lambda x: None,  # Disable default item display
    ) as bar:
        current_podcast = None
        for podcast, episode in bar:
            # Show podcast header when switching podcasts
            if current_podcast != podcast.title:
                click.echo(f"\n📻 {podcast.title}")
                click.echo("─" * 50)
                current_podcast = podcast.title

            click.echo(f"\n🎧 {episode.title}")

            try:
                audio_path = downloader.download_episode(episode, podcast)

                if audio_path:
                    # Get accurate duration from the downloaded file
                    full_audio_path = path_manager.original_audio_file(audio_path)
                    duration_seconds = get_audio_duration(full_audio_path)

                    # Store the relative path (includes podcast subdirectory)
                    feed_manager.mark_episode_downloaded(
                        str(podcast.rss_url), episode.external_id, audio_path, duration=duration_seconds
                    )
                    downloaded_count += 1
                    click.echo("✅ Downloaded successfully")

                    # Also download external transcripts if available (for evaluation/debugging)
                    external_downloader = ctx.obj.external_transcript_downloader
                    transcript_results = external_downloader.download_all_for_episode(
                        episode_id=episode.id,
                        podcast_slug=podcast.slug,
                        episode_slug=episode.slug,
                    )
                    if transcript_results:
                        formats = list(transcript_results.keys())
                        click.echo(f"📝 Downloaded external transcripts: {', '.join(formats)}")
                else:
                    click.echo("❌ Download failed")

            except Exception as e:
                click.echo(f"❌ Error downloading: {e}")
                continue

    total_time = time.time() - start_time
    click.echo("\n🎉 Download complete!")
    click.echo(f"✓ {downloaded_count} episode(s) downloaded in {total_time:.1f} seconds")
    if downloaded_count > 0:
        click.echo("💡 Next step: Run 'thestill downsample' to prepare audio for transcription")


@main.command()
@click.option("--podcast-id", help="Podcast ID (RSS URL or index) to downsample")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to downsample")
@click.option("--dry-run", "-d", is_flag=True, help="Preview what would be downsampled")
@click.pass_context
@require_config
@log_command
def downsample(ctx, podcast_id, max_episodes, dry_run):
    """Downsample downloaded audio to 16kHz, 16-bit, mono WAV format"""
    # Use shared services from context
    config = ctx.obj.config
    podcast_service = ctx.obj.podcast_service
    feed_manager = ctx.obj.feed_manager
    preprocessor = ctx.obj.audio_preprocessor

    click.echo("🔍 Looking for episodes to downsample...")

    # Get episodes that need downsampling
    episodes_to_downsample = feed_manager.get_episodes_to_downsample(str(config.storage_path))

    if not episodes_to_downsample:
        click.echo("✓ No episodes found that need downsampling")
        return

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)

        episodes_to_downsample = [
            (p, eps) for p, eps in episodes_to_downsample if str(p.rss_url) == str(podcast.rss_url)
        ]

        if not episodes_to_downsample:
            click.echo(f"✓ No episodes need downsampling for podcast: {podcast.title}")
            return

    # Apply max_episodes limit
    if max_episodes:
        total = 0
        filtered = []
        for podcast, episodes in episodes_to_downsample:
            remaining = max_episodes - total
            if remaining <= 0:
                break
            filtered.append((podcast, episodes[:remaining]))
            total += len(episodes[:remaining])
        episodes_to_downsample = filtered

    # Count total episodes
    total_count = sum(len(eps) for _, eps in episodes_to_downsample)
    click.echo(f"🔧 Found {total_count} episode(s) to downsample")

    if dry_run:
        for podcast, episodes in episodes_to_downsample:
            click.echo(f"\n📻 {podcast.title}")
            for episode in episodes:
                click.echo(f"  • {episode.title}")
        click.echo("\n(Run without --dry-run to actually downsample)")
        return

    # Downsample episodes
    downsampled_count = 0
    start_time = time.time()

    # Flatten episodes for progress bar
    all_episodes = []
    for podcast, episodes in episodes_to_downsample:
        for episode in episodes:
            all_episodes.append((podcast, episode))

    # Progress bar wrapper
    with click.progressbar(
        all_episodes,
        label="Downsampling",
        show_pos=True,  # Show "X/Y" counter
        show_eta=True,  # Show estimated time
        file=sys.stderr,  # Use stderr (consistent with logging)
        item_show_func=lambda x: None,  # Disable default item display
    ) as bar:
        current_podcast = None
        for podcast, episode in bar:
            # Show podcast header when switching podcasts
            if current_podcast != podcast.title:
                click.echo(f"\n📻 {podcast.title}")
                click.echo("─" * 50)
                current_podcast = podcast.title

            click.echo(f"\n🎧 {episode.title}")

            try:
                # Build paths
                original_audio_file = config.path_manager.original_audio_file(episode.audio_path)

                # Verify file exists before downsampling
                try:
                    config.path_manager.require_file_exists(original_audio_file, "Original audio file not found")
                except FileNotFoundError:
                    click.echo(f"❌ Original audio file not found: {episode.audio_path}")
                    continue

                # Determine output directory - use podcast subdirectory if audio_path has one
                audio_path_obj = Path(episode.audio_path)
                if len(audio_path_obj.parts) > 1:
                    # audio_path is like "podcast-slug/episode_hash.mp3", use podcast-slug subdirectory
                    podcast_subdir = audio_path_obj.parent
                    output_dir = config.path_manager.downsampled_audio_dir() / podcast_subdir
                else:
                    # Flat structure (legacy) - use podcast slug for new subdirectory
                    output_dir = config.path_manager.downsampled_audio_dir() / podcast.slug

                output_dir.mkdir(parents=True, exist_ok=True)

                # Downsample
                click.echo("🔧 Downsampling to 16kHz, 16-bit, mono WAV...")
                downsampled_path = preprocessor.downsample_audio(str(original_audio_file), str(output_dir))

                if downsampled_path:
                    # Store relative path: podcast-slug/filename.wav
                    downsampled_path_obj = Path(downsampled_path)
                    relative_path = f"{output_dir.name}/{downsampled_path_obj.name}"

                    # Get accurate duration from the downsampled file
                    duration_seconds = get_audio_duration(downsampled_path)

                    feed_manager.mark_episode_downsampled(
                        str(podcast.rss_url), episode.external_id, relative_path, duration=duration_seconds
                    )
                    downsampled_count += 1
                    click.echo("✅ Downsampled successfully")

                    # Auto-cleanup: delete original audio file after successful downsampling
                    if config.delete_audio_after_processing:
                        downloader = ctx.obj.audio_downloader
                        if downloader.delete_audio_file(episode):
                            # Clear the path in database since file no longer exists
                            feed_manager.clear_episode_audio_path(str(podcast.rss_url), episode.external_id)
                            click.echo("🗑️  Cleaned up original audio file")
                else:
                    click.echo("❌ Downsampling failed")

            except Exception as e:
                click.echo(f"❌ Error downsampling: {e}")
                import traceback

                traceback.print_exc()
                continue

    total_time = time.time() - start_time
    click.echo("\n🎉 Downsampling complete!")
    click.echo(f"✓ {downsampled_count} episode(s) downsampled in {total_time:.1f} seconds")


@main.command("clean-transcript")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be processed")
@click.option("--max-episodes", "-m", default=5, help="Maximum episodes to process")
@click.option("--force", "-f", is_flag=True, help="Re-process even if clean transcript exists")
@click.option("--stream", "-s", is_flag=True, help="Stream LLM output in real-time")
@click.pass_context
@require_config
@log_command
def clean_transcript(ctx, dry_run, max_episodes, force, stream):
    """Clean transcripts using facts-based two-pass approach"""
    import json

    from .core.transcript_cleaning_processor import TranscriptCleaningProcessor

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager

    # Create LLM provider
    try:
        llm_provider = create_llm_provider_from_config(config)
        click.echo(f"✓ Using {config.llm_provider.upper()} provider with model: {llm_provider.get_model_name()}")
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    cleaning_processor = TranscriptCleaningProcessor(llm_provider)

    # Find transcripts to clean
    click.echo("🔍 Looking for transcripts to clean...")

    podcasts = feed_manager.list_podcasts()
    transcripts_to_clean = []

    for podcast in podcasts:
        for episode in podcast.episodes:
            if episode.raw_transcript_path:
                transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
                if not transcript_path.exists():
                    continue

                # Check if already cleaned (unless force)
                if not force and episode.clean_transcript_path:
                    clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
                    if clean_path.exists():
                        continue

                transcripts_to_clean.append((podcast, episode, transcript_path))

    if not transcripts_to_clean:
        click.echo("✓ No transcripts found to clean")
        return

    total_transcripts = min(len(transcripts_to_clean), max_episodes)
    click.echo(f"📄 Found {len(transcripts_to_clean)} transcripts. Processing {total_transcripts} episodes")

    if dry_run:
        for podcast, episode, _ in transcripts_to_clean[:max_episodes]:
            click.echo(f"  • {podcast.title}: {episode.title}")
        click.echo("\n(Run without --dry-run to process)")
        return

    total_processed = 0
    start_time = time.time()

    # Create streaming callback if enabled (defined once, outside loop)
    def _stream_chunk(chunk: str) -> None:
        """Print LLM output chunks in real-time."""
        sys.stdout.write(chunk)
        sys.stdout.flush()

    stream_callback = _stream_chunk if stream else None

    for podcast, episode, transcript_path in transcripts_to_clean[:max_episodes]:
        click.echo(f"\n📻 {podcast.title}")
        click.echo(f"🎧 {episode.title}")
        click.echo("─" * 50)

        try:
            # Load transcript
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)

            # Generate output path using podcast subdirectory structure
            base_name = transcript_path.stem
            if base_name.endswith("_transcript"):
                base_name = base_name[: -len("_transcript")]

            # Extract episode_slug_hash from base_name (format: podcast-slug_episode-slug_hash)
            # We want just episode-slug_hash for the filename
            parts = base_name.split("_")
            if len(parts) >= 3:
                # Last part is hash, everything between first and last is episode slug
                episode_slug_hash = "_".join(parts[1:])  # episode-slug_hash
            else:
                episode_slug_hash = base_name

            # Create podcast subdirectory
            podcast_subdir = path_manager.clean_transcripts_dir() / podcast.slug
            podcast_subdir.mkdir(parents=True, exist_ok=True)

            cleaned_filename = f"{episode_slug_hash}_cleaned.md"
            cleaned_path = podcast_subdir / cleaned_filename

            # Database stores relative path: {podcast_slug}/{filename}
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
                on_stream_chunk=stream_callback,
                language=podcast.language,
            )

            # Add newline after streaming completes
            if stream:
                click.echo("")  # End the streamed output with newline

            if result:
                # Derive the DB-relative JSON sidecar path from the MD
                # filename — the processor writes it alongside the MD
                # when the segmented pipeline is the primary producer.
                # Only set it on the DB row when that pipeline actually
                # ran; otherwise we'd point at a non-existent file.
                clean_transcript_json_db_path: Optional[str] = None
                if result.get("cleaned_json_path"):
                    json_filename = f"{Path(cleaned_filename).stem}.json"
                    clean_transcript_json_db_path = f"{podcast.slug}/{json_filename}"

                # Update feed manager
                # Note: raw_transcript_path is preserved (episode.raw_transcript_path)
                feed_manager.mark_episode_processed(
                    str(podcast.rss_url),
                    episode.external_id,
                    raw_transcript_path=episode.raw_transcript_path,
                    clean_transcript_path=clean_transcript_db_path,
                    clean_transcript_json_path=clean_transcript_json_db_path,
                )

                total_processed += 1
                click.echo("✅ Transcript cleaned successfully!")
                click.echo(f"👥 Speakers: {len(result['episode_facts'].speaker_mapping)}")

        except Exception as e:
            click.echo(f"❌ Error: {e}")
            import traceback

            traceback.print_exc()
            continue

    total_time = time.time() - start_time
    click.echo("\n🎉 Processing complete!")
    click.echo(f"✓ {total_processed} transcripts cleaned in {total_time:.1f} seconds")


# ============================================================================
# Facts management commands (for transcript cleaning)
# ============================================================================


@main.group()
@click.pass_context
def facts(ctx):
    """Manage podcast and episode facts for transcript cleaning"""
    pass


@facts.command("list")
@click.pass_context
@require_config
def facts_list(ctx):
    """List all facts files"""
    path_manager = ctx.obj.path_manager

    # List podcast facts
    podcast_facts_dir = path_manager.podcast_facts_dir()
    episode_facts_dir = path_manager.episode_facts_dir()

    click.echo(CLIFormatter.format_header("Facts Files"))

    # Podcast facts
    click.echo("\n📻 Podcast Facts:")
    if podcast_facts_dir.exists():
        podcast_files = list(podcast_facts_dir.glob("*.facts.md"))
        if podcast_files:
            for f in sorted(podcast_files):
                click.echo(f"  • {f.name}")
        else:
            click.echo("  (no podcast facts files)")
    else:
        click.echo("  (directory not created)")

    # Episode facts (now in subdirectories by podcast)
    click.echo("\n🎧 Episode Facts:")
    if episode_facts_dir.exists():
        # List podcast subdirectories
        podcast_subdirs = [d for d in episode_facts_dir.iterdir() if d.is_dir()]
        if podcast_subdirs:
            total_files = 0
            for podcast_dir in sorted(podcast_subdirs):
                episode_files = list(podcast_dir.glob("*.facts.md"))
                if episode_files:
                    total_files += len(episode_files)
                    click.echo(f"  📻 {podcast_dir.name}/ ({len(episode_files)} episodes)")
                    # Show first 3 per podcast
                    for f in sorted(episode_files)[:3]:
                        click.echo(f"     • {f.name}")
                    if len(episode_files) > 3:
                        click.echo(f"     ... and {len(episode_files) - 3} more")
            if total_files == 0:
                click.echo("  (no episode facts files)")
        else:
            click.echo("  (no episode facts files)")
    else:
        click.echo("  (directory not created)")


@facts.command("show")
@click.option("--podcast-id", "-p", help="Podcast ID (index or URL)")
@click.option("--episode-id", "-e", help="Episode ID (index, 'latest', or slug)")
@click.pass_context
@require_config
def facts_show(ctx, podcast_id, episode_id):
    """Show facts for a podcast or episode"""
    from .core.facts_manager import FactsManager
    from .utils.slug import generate_slug

    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository
    podcast_service = ctx.obj.podcast_service
    facts_manager = FactsManager(path_manager)

    if episode_id and podcast_id:
        # Show episode facts - need both podcast and episode to build path
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}")
            ctx.exit(1)

        episode = podcast_service.get_episode(podcast_id, episode_id)
        if not episode:
            click.echo(f"❌ Episode not found: {episode_id}")
            ctx.exit(1)

        episode_facts = facts_manager.load_episode_facts(podcast.slug, episode.slug)
        if episode_facts:
            click.echo(CLIFormatter.format_header(f"Episode Facts: {episode_facts.episode_title}"))
            click.echo(facts_manager.get_episode_facts_markdown(podcast.slug, episode.slug))
        else:
            click.echo(f"❌ No facts file found for episode: {episode.title}")
            click.echo(f"   Expected file: {facts_manager.get_episode_facts_path(podcast.slug, episode.slug)}")
            ctx.exit(1)

    elif podcast_id:
        # Get podcast
        podcast = None
        if podcast_id.isdigit():
            podcast = repository.get_by_index(int(podcast_id))
        elif podcast_id.startswith("http"):
            podcast = repository.get_by_url(podcast_id)

        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}")
            ctx.exit(1)

        podcast_slug = generate_slug(podcast.title)
        podcast_facts = facts_manager.load_podcast_facts(podcast_slug)

        if podcast_facts:
            click.echo(CLIFormatter.format_header(f"Podcast Facts: {podcast_facts.podcast_title}"))
            click.echo(facts_manager.get_podcast_facts_markdown(podcast_slug))
        else:
            click.echo(f"❌ No facts file found for podcast: {podcast.title}")
            click.echo(f"   Expected file: {facts_manager.get_podcast_facts_path(podcast_slug)}")
            ctx.exit(1)
    else:
        click.echo("❌ Please specify --podcast-id or --episode-id")
        ctx.exit(1)


@facts.command("edit")
@click.option("--podcast-id", "-p", help="Podcast ID (index or URL)")
@click.option("--episode-id", "-e", help="Episode ID (index, 'latest', or slug)")
@click.pass_context
@require_config
def facts_edit(ctx, podcast_id, episode_id):
    """Open facts file in $EDITOR"""
    import os
    import subprocess

    from .core.facts_manager import FactsManager
    from .utils.slug import generate_slug

    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository
    podcast_service = ctx.obj.podcast_service
    facts_manager = FactsManager(path_manager)

    editor = os.environ.get("EDITOR", "nano")
    file_path = None

    if episode_id and podcast_id:
        # Episode facts - need both podcast and episode
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}")
            ctx.exit(1)

        episode = podcast_service.get_episode(podcast_id, episode_id)
        if not episode:
            click.echo(f"❌ Episode not found: {episode_id}")
            ctx.exit(1)

        file_path = facts_manager.get_episode_facts_path(podcast.slug, episode.slug)
    elif podcast_id:
        podcast = None
        if podcast_id.isdigit():
            podcast = repository.get_by_index(int(podcast_id))
        elif podcast_id.startswith("http"):
            podcast = repository.get_by_url(podcast_id)

        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}")
            ctx.exit(1)

        podcast_slug = generate_slug(podcast.title)
        file_path = facts_manager.get_podcast_facts_path(podcast_slug)
    else:
        click.echo("❌ Please specify --podcast-id or --episode-id")
        ctx.exit(1)

    if not file_path.exists():
        click.echo(f"❌ Facts file not found: {file_path}")
        click.echo("   Run clean-transcript first to generate facts.")
        ctx.exit(1)

    click.echo(f"Opening {file_path} with {editor}...")
    subprocess.run([editor, str(file_path)])


@facts.command("extract")
@click.option("--podcast-id", "-p", required=True, help="Podcast ID (index or URL)")
@click.option("--episode-id", "-e", help="Episode UUID (or 'latest')")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing facts")
@click.pass_context
@require_config
def facts_extract(ctx, podcast_id, episode_id, force):
    """Extract facts from a transcript"""
    import json

    from .core.facts_extractor import FactsExtractor
    from .core.facts_manager import FactsManager
    from .core.llm_provider import create_llm_provider
    from .utils.slug import generate_slug

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository
    facts_manager = FactsManager(path_manager)

    # Get podcast
    podcast = None
    if podcast_id.isdigit():
        podcast = repository.get_by_index(int(podcast_id))
    elif podcast_id.startswith("http"):
        podcast = repository.get_by_url(podcast_id)

    if not podcast:
        click.echo(f"❌ Podcast not found: {podcast_id}")
        ctx.exit(1)

    # Get episode
    episode = None
    if episode_id == "latest":
        # Find most recent episode with transcript
        for ep in sorted(podcast.episodes, key=lambda e: e.pub_date or e.created_at, reverse=True):
            if ep.raw_transcript_path:
                episode = ep
                break
    elif episode_id:
        episode = repository.get_episode(episode_id)
    else:
        # Find first episode with transcript
        for ep in podcast.episodes:
            if ep.raw_transcript_path:
                episode = ep
                break

    if not episode:
        click.echo(f"❌ No episode with transcript found")
        ctx.exit(1)

    # Check existing facts
    podcast_slug = podcast.slug or generate_slug(podcast.title)
    episode_slug = episode.slug or generate_slug(episode.title)
    if not force:
        if facts_manager.load_episode_facts(podcast_slug, episode_slug):
            click.echo(f"❌ Episode facts already exist. Use --force to overwrite.")
            ctx.exit(1)

    # Load transcript
    transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
    if not transcript_path.exists():
        click.echo(f"❌ Transcript file not found: {transcript_path}")
        ctx.exit(1)

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_data = json.load(f)

    # Create LLM provider
    try:
        llm_provider = create_llm_provider_from_config(config)
        click.echo(f"✓ Using {config.llm_provider.upper()} provider")
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    # Extract facts
    click.echo(f"📻 {podcast.title}")
    click.echo(f"🎧 {episode.title}")
    click.echo("─" * 50)

    facts_extractor = FactsExtractor(llm_provider)

    # Load existing podcast facts (for context)
    podcast_facts = facts_manager.load_podcast_facts(podcast_slug)

    click.echo("Extracting episode facts...")
    episode_facts = facts_extractor.extract_episode_facts(
        transcript_data=transcript_data,
        podcast_title=podcast.title,
        podcast_description=podcast.description,
        episode_title=episode.title,
        episode_description=episode.description,
        podcast_facts=podcast_facts,
        language=podcast.language,
    )

    # Save episode facts
    facts_manager.save_episode_facts(podcast_slug, episode_slug, episode_facts)
    click.echo(f"✓ Saved episode facts: {facts_manager.get_episode_facts_path(podcast_slug, episode_slug)}")

    # Extract podcast facts if not present
    if not podcast_facts:
        click.echo("Extracting podcast facts (first episode)...")
        podcast_facts = facts_extractor.extract_initial_podcast_facts(
            transcript_data=transcript_data,
            podcast_title=podcast.title,
            podcast_description=podcast.description,
            episode_facts=episode_facts,
            language=podcast.language,
        )
        facts_manager.save_podcast_facts(podcast_slug, podcast_facts)
        click.echo(f"✓ Saved podcast facts: {facts_manager.get_podcast_facts_path(podcast_slug)}")

    click.echo("\n✅ Facts extraction complete!")
    click.echo(f"   Speakers identified: {len(episode_facts.speaker_mapping)}")
    click.echo(f"   Guests: {len(episode_facts.guests)}")
    click.echo(f"   Topics: {len(episode_facts.topics_keywords)}")


@main.command()
@click.pass_context
@require_config
@log_command
def status(ctx):
    """Show system status and statistics"""
    # Use shared services from context
    config = ctx.obj.config
    stats_service = ctx.obj.stats_service

    click.echo(CLIFormatter.format_header("Thestill Status"))

    # Get statistics from service
    stats = stats_service.get_stats()

    # Storage info
    click.echo(f"Storage path: {stats.storage_path}")
    click.echo(f"Audio files: {stats.audio_files_count} files")
    click.echo(f"Transcripts available: {stats.transcripts_available} files")

    # Configuration
    click.echo("\nConfiguration:")

    # Transcription settings
    click.echo(f"  Transcription provider: {config.transcription_provider}")
    if config.transcription_provider == "whisper":
        click.echo(f"  Whisper model: {config.whisper_model}")
        click.echo(f"  Whisper device: {config.whisper_device}")
    elif config.transcription_provider == "google":
        click.echo(f"  Google Cloud project: {config.google_cloud_project_id or 'Not set'}")
        click.echo(f"  Google Cloud bucket: {config.google_storage_bucket or 'Auto'}")
    elif config.transcription_provider == "elevenlabs":
        click.echo(f"  ElevenLabs model: {config.elevenlabs_model}")
        click.echo(f"  ElevenLabs API key: {'✓ Set' if config.elevenlabs_api_key else '✗ Not set'}")

    # Diarization settings
    click.echo(f"  Speaker diarization: {'✓ Enabled' if config.enable_diarization else '✗ Disabled'}")
    if config.enable_diarization:
        if config.min_speakers or config.max_speakers:
            speakers_range = f"{config.min_speakers or 'auto'}-{config.max_speakers or 'auto'}"
            click.echo(f"  Speaker range: {speakers_range}")
        if config.transcription_provider == "whisper":
            click.echo(f"  Diarization model: {config.diarization_model}")

    # LLM settings
    click.echo(f"  LLM provider: {config.llm_provider}")
    # Create provider instances directly to avoid health checks in create_llm_provider
    if config.llm_provider == "openai":
        from thestill.core.llm_provider import OpenAIProvider

        try:
            llm_provider = OpenAIProvider(api_key=config.openai_api_key, model=config.openai_model)
            click.echo(f"  LLM model: {llm_provider.get_model_display_name()}")
        except Exception:
            click.echo(f"  LLM model: OpenAI {config.openai_model}")
    elif config.llm_provider == "ollama":
        from thestill.core.llm_provider import OllamaProvider

        try:
            llm_provider = OllamaProvider(base_url=config.ollama_base_url, model=config.ollama_model)
            click.echo(f"  LLM model: {llm_provider.get_model_display_name()}")
        except Exception:
            click.echo(f"  LLM model: Ollama {config.ollama_model}")
        click.echo(f"  Ollama URL: {config.ollama_base_url}")
    elif config.llm_provider == "gemini":
        from thestill.core.llm_provider import GeminiProvider

        try:
            llm_provider = GeminiProvider(api_key=config.gemini_api_key, model=config.gemini_model)
            click.echo(f"  LLM model: {llm_provider.get_model_display_name()}")
        except Exception:
            click.echo(f"  LLM model: Google {config.gemini_model}")
    elif config.llm_provider == "anthropic":
        from thestill.core.llm_provider import AnthropicProvider

        try:
            llm_provider = AnthropicProvider(api_key=config.anthropic_api_key, model=config.anthropic_model)
            click.echo(f"  LLM model: {llm_provider.get_model_display_name()}")
        except Exception:
            click.echo(f"  LLM model: Anthropic {config.anthropic_model}")

    # Transcript cleaning settings
    if config.enable_transcript_cleaning:
        click.echo(f"  Transcript cleaning: ✓ Enabled ({config.cleaning_provider}/{config.cleaning_model})")

    # Processing settings
    click.echo(f"  Max workers: {config.max_workers}")
    if config.max_episodes_per_podcast:
        click.echo(f"  Max episodes per podcast: {config.max_episodes_per_podcast}")

    # Podcast stats with pipeline breakdown
    click.echo("\nPodcast Statistics:")
    click.echo(f"  Tracked podcasts: {stats.podcasts_tracked}")
    click.echo(f"  Total episodes: {stats.episodes_total}")
    click.echo("")
    click.echo("  Pipeline Progress:")
    click.echo(f"    ○ Discovered (not downloaded):  {stats.episodes_discovered}")
    click.echo(f"    ↓ Downloaded:                   {stats.episodes_downloaded}")
    click.echo(f"    ♪ Downsampled:                  {stats.episodes_downsampled}")
    click.echo(f"    ✎ Transcribed:                  {stats.episodes_transcribed}")
    click.echo(f"    ✓ Cleaned:                      {stats.episodes_cleaned}")
    click.echo(f"    ★ Summarized (fully processed): {stats.episodes_summarized}")
    click.echo("")
    click.echo(
        f"  Summary: {stats.episodes_summarized}/{stats.episodes_total} fully processed ({stats.episodes_unprocessed} in progress)"
    )
    click.echo("")
    click.echo("Corpus chunk index (sqlite-vec):")
    click.echo(f"  Chunks indexed:  {stats.chunks_count:,}")
    click.echo(f"  Embedding model: {stats.embedding_model or '(none — run `thestill chunks backfill`)'}")
    click.echo("")
    click.echo("Entity extraction:")
    click.echo(f"  Skipped (legacy, no JSON sidecar): {stats.episodes_skipped_legacy:,}")

    # Show pending Google Cloud transcription operations (if using Google provider)
    if config.transcription_provider.lower() == "google":
        try:
            transcriber = GoogleCloudTranscriber(
                credentials_path=config.google_app_credentials or None,
                project_id=config.google_cloud_project_id or None,
                storage_bucket=config.google_storage_bucket or None,
                enable_diarization=config.enable_diarization,
                min_speakers=config.min_speakers,
                max_speakers=config.max_speakers,
                path_manager=config.path_manager,
                _quiet=True,
            )
            pending_ops = transcriber.list_pending_operations()
            if pending_ops:
                click.echo("\n⏳ Pending Transcription Operations:")
                for op in pending_ops:
                    age_hours = (datetime.now() - op.created_at).total_seconds() / 3600
                    click.echo(f"   • {op.podcast_slug}/{op.episode_slug}")
                    click.echo(f"     Started: {age_hours:.1f} hours ago")
                    click.echo(f"     Operation: {op.operation_id[:16]}...")
                click.echo(f"\n   Run 'thestill transcribe' to check and download completed operations")
        except Exception:
            pass  # Silently skip if Google Cloud is not configured


@main.command()
@click.option("--limit", "-l", type=int, default=20, help="Number of items to show (default: 20)")
@click.pass_context
@require_config
@log_command
def activity(ctx, limit):
    """Show recent processing activity log"""
    # Get activity from service
    items = ctx.obj.stats_service.get_recent_activity(limit=limit)

    # Format and display
    output = CLIFormatter.format_activity_log(items)
    click.echo(output)


@main.command()
@click.option("--dry-run", is_flag=True, help="Preview what would be deleted without actually deleting")
@click.pass_context
@require_config
@log_command
def cleanup(ctx, dry_run):
    """Clean up old audio files and sync database.

    Removes audio files older than CLEANUP_DAYS from both original_audio/
    and downsampled_audio/ directories. Also clears the corresponding
    database paths so episodes can be re-downloaded if needed.

    Episodes that are already transcribed will NOT be re-transcribed
    (transcript paths are preserved).
    """
    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository

    if dry_run:
        click.echo(f"🧹 [DRY RUN] Previewing cleanup of files older than {config.cleanup_days} days...")
    else:
        click.echo(f"🧹 Cleaning up files older than {config.cleanup_days} days...")

    cutoff_time = time.time() - (config.cleanup_days * 24 * 60 * 60)

    # Collect files to delete from both directories
    # Store relative paths (e.g., "podcast-slug/episode.mp3") to match database paths
    deleted_original = []  # List of relative paths deleted from original_audio
    deleted_downsampled = []  # List of relative paths deleted from downsampled_audio

    # Process original_audio directory
    original_dir = path_manager.original_audio_dir()
    if original_dir.exists():
        for file_path in original_dir.glob("**/*"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                # Get relative path from original_audio dir (e.g., "podcast-slug/episode.mp3")
                relative_path = str(file_path.relative_to(original_dir))
                if dry_run:
                    click.echo(f"  Would delete: original_audio/{relative_path}")
                else:
                    try:
                        file_path.unlink()
                        click.echo(f"  Deleted: original_audio/{relative_path}")
                    except Exception as e:
                        click.echo(f"  ⚠️ Error deleting {relative_path}: {e}", err=True)
                        continue
                deleted_original.append(relative_path)

    # Process downsampled_audio directory
    downsampled_dir = path_manager.downsampled_audio_dir()
    if downsampled_dir.exists():
        for file_path in downsampled_dir.glob("**/*"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                # Get relative path from downsampled_audio dir
                relative_path = str(file_path.relative_to(downsampled_dir))
                if dry_run:
                    click.echo(f"  Would delete: downsampled_audio/{relative_path}")
                else:
                    try:
                        file_path.unlink()
                        click.echo(f"  Deleted: downsampled_audio/{relative_path}")
                    except Exception as e:
                        click.echo(f"  ⚠️ Error deleting {relative_path}: {e}", err=True)
                        continue
                deleted_downsampled.append(relative_path)

    total_files = len(deleted_original) + len(deleted_downsampled)

    if total_files == 0:
        click.echo("✓ No files to delete")
        return

    # Sync database - clear paths for deleted files
    # This preserves transcript paths so already-transcribed episodes won't be re-processed
    db_updates = 0
    podcasts = repository.get_all()

    for podcast in podcasts:
        for episode in podcast.episodes:
            updates = {}

            # Check if original audio was deleted
            if episode.audio_path and episode.audio_path in deleted_original:
                updates["audio_path"] = None

            # Check if downsampled audio was deleted
            if episode.downsampled_audio_path and episode.downsampled_audio_path in deleted_downsampled:
                updates["downsampled_audio_path"] = None

            # Apply updates if any
            if updates:
                if dry_run:
                    click.echo(f"  Would clear DB paths for: {episode.title[:50]}...")
                else:
                    repository.update_episode(str(podcast.rss_url), episode.external_id, updates)
                    click.echo(f"  Cleared DB paths for: {episode.title[:50]}...")
                db_updates += 1

    if dry_run:
        click.echo(f"\n✓ Would delete {total_files} file(s) and update {db_updates} episode(s) (dry-run mode)")
    else:
        click.echo(f"\n✓ Cleanup complete - deleted {total_files} file(s), updated {db_updates} episode(s)")


@main.command()
@click.argument("audio_path", type=click.Path(exists=True), required=False)
@click.option("--downsample", is_flag=True, help="Enable audio downsampling (16kHz, mono, 16-bit)")
@click.option("--podcast-id", help="Transcribe episodes from specific podcast (index or RSS URL)")
@click.option("--episode-id", help="Transcribe specific episode (requires --podcast-id)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to transcribe")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be transcribed without transcribing")
@click.option(
    "--cancel-pending",
    is_flag=True,
    help="Download completed operations and CANCEL still-running ones (don't wait)",
)
@click.option(
    "--language",
    "-l",
    default="en",
    help="Language code for standalone transcription (ISO 639-1, e.g., 'en', 'hr'). Ignored for batch mode.",
)
@click.pass_context
@require_config
@log_command
def transcribe(ctx, audio_path, downsample, podcast_id, episode_id, max_episodes, dry_run, cancel_pending, language):
    """Transcribe audio files to JSON transcripts.

    Without arguments: Transcribes all downloaded episodes that need transcription.
    With audio_path: Transcribes a specific audio file (standalone mode).
    """
    config = ctx.obj.config
    preprocessor = ctx.obj.audio_preprocessor

    # Initialize the appropriate transcriber based on config settings.
    # Construction is delegated to core.transcriber_factory so CLI, web, and
    # MCP stay in lockstep; the CLI layers provider-specific UX (echoes,
    # webhook-server startup, pending-ops inspection) on top.
    from .core.transcriber_factory import create_transcriber
    from .utils.exceptions import ThestillError

    provider = config.transcription_provider.lower()
    provider_intros = {
        "google": "🎤 Using Google Cloud Speech-to-Text",
        "elevenlabs": "🎤 Using ElevenLabs Speech-to-Text",
        "dalston": "🎤 Using Dalston Speech-to-Text",
        "parakeet": "🎤 Using NVIDIA Parakeet Speech-to-Text",
    }
    if provider in provider_intros:
        click.echo(provider_intros[provider])
    elif config.enable_diarization:
        click.echo("🎤 Using WhisperX with speaker diarization enabled")
    else:
        click.echo(f"🎤 Using Whisper model: {config.whisper_model}")

    # Credential / config validation with a friendly exit for the CLI.
    try:
        from .core.transcriber_factory import validate_transcription_provider

        validate_transcription_provider(config)
    except ThestillError as e:
        click.echo(f"❌ {e}", err=True)
        ctx.exit(1)

    # ElevenLabs: start the webhook server before constructing the transcriber
    # so the transcriber can be told whether to wait for callbacks vs poll.
    elevenlabs_use_async = False
    elevenlabs_wait_for_webhook = False
    if provider == "elevenlabs":
        from .web import BackgroundWebhookServer, ExistingServerInfo, webhook_server_context

        webhook_server = webhook_server_context(
            config=config,
            port=config.webhook_server_port,
        )
        ctx.obj.webhook_server_context = webhook_server
        server = webhook_server.__enter__()
        ctx.obj.started_webhook_server = isinstance(server, BackgroundWebhookServer)

        webhook_available = isinstance(server, (BackgroundWebhookServer, ExistingServerInfo))

        if isinstance(server, BackgroundWebhookServer):
            click.echo(f"🌐 Webhook server started on port {config.webhook_server_port}")
            click.echo(f"   Webhook URL: {server.webhook_url}")
        elif isinstance(server, ExistingServerInfo):
            click.echo(f"✅ thestill server already running on port {config.webhook_server_port}")
            click.echo(f"   Webhook URL: {server.webhook_url}")
        else:
            click.echo(
                f"⚠️  Port {config.webhook_server_port} in use by another service - "
                "will use polling mode instead of webhooks",
                err=True,
            )

        elevenlabs_use_async = True
        elevenlabs_wait_for_webhook = webhook_available
        ctx.obj.using_webhook_mode = webhook_available

    try:
        transcriber = create_transcriber(
            config,
            path_manager=config.path_manager,
            console=ctx.obj.console,
            elevenlabs_use_async=elevenlabs_use_async,
            elevenlabs_async_threshold_mb=config.elevenlabs_async_threshold_mb,
            elevenlabs_tag_audio_events=True,
            elevenlabs_wait_for_webhook=elevenlabs_wait_for_webhook,
        )
    except ImportError as e:
        click.echo(f"❌ {e}", err=True)
        install_hints = {
            "google": "   Install with: pip install google-cloud-speech google-cloud-storage",
            "dalston": ("   Install with: pip install " "git+https://github.com/ssarunic/dalston.git#subdirectory=sdk"),
        }
        if provider in install_hints:
            click.echo(install_hints[provider], err=True)
        ctx.exit(1)

    # Provider-specific post-construction UX.
    if provider == "dalston":
        click.echo(f"   Server: {config.dalston_base_url}")
        if config.dalston_model:
            click.echo(f"   Model: {config.dalston_model}")
    elif provider == "google":
        # For chunked transcriptions, individual chunks are persisted as
        # operations and picked up by _transcribe_chunked on the next run.
        # Here we only surface their presence and handle --cancel-pending.
        pending_ops = transcriber.list_pending_operations()
        if pending_ops:
            episodes_with_pending = set((op.podcast_slug, op.episode_slug) for op in pending_ops)
            click.echo(
                f"\n⏳ Found {len(pending_ops)} pending chunk operation(s) for {len(episodes_with_pending)} episode(s)"
            )
            for podcast_slug, episode_slug in sorted(episodes_with_pending):
                chunk_count = sum(
                    1 for op in pending_ops if op.podcast_slug == podcast_slug and op.episode_slug == episode_slug
                )
                click.echo(f"   • {podcast_slug}/{episode_slug} ({chunk_count} chunk(s))")

            if cancel_pending:
                click.echo("\n⏹ Cancelling pending operations...")
                results = transcriber.reset_pending_operations()

                completed_count = sum(1 for op, data in results if data is not None)
                cancelled_count = sum(1 for op, data in results if data is None and op.state.value == "pending")
                failed_count = sum(1 for op, data in results if op.state.value == "failed")

                if completed_count > 0:
                    click.echo(f"   Downloaded {completed_count} completed chunk(s)")
                if cancelled_count > 0:
                    click.echo(f"   Cancelled {cancelled_count} in-progress chunk(s)")
                if failed_count > 0:
                    click.echo(f"   {failed_count} chunk(s) had failed")
                click.echo("   Note: Cancelled episodes will restart from scratch on next run")
                click.echo("")
            else:
                click.echo("   These will be resumed when the episode is transcribed again.")
                click.echo("   (Use --cancel-pending to discard and start fresh)")
                click.echo("")

    # Mode 1: Standalone file transcription
    if audio_path:
        if episode_id:
            click.echo("⚠️  --episode-id is ignored when audio_path is provided", err=True)

        # Determine output path
        audio_path_obj = Path(audio_path)
        output = str(config.path_manager.raw_transcript_file(f"{audio_path_obj.stem}_transcript.json"))

        try:
            # Preprocess audio if needed
            transcription_audio_path = audio_path
            preprocessed_audio_path = None

            if downsample:
                click.echo("🔧 Downsampling audio for optimal transcription...")
                preprocessed_audio_path = preprocessor.preprocess_audio(audio_path)
                if preprocessed_audio_path and preprocessed_audio_path != audio_path:
                    transcription_audio_path = preprocessed_audio_path

            # Transcribe
            click.echo(f"📝 Transcribing audio file: {Path(audio_path).name}")

            # Prepare cleaning config if enabled (legacy cleaning during transcription)
            cleaning_config = None
            if config.enable_transcript_cleaning:
                cleaning_config = {
                    "provider": config.cleaning_provider,
                    "model": config.cleaning_model,
                    "thinking_level": config.gemini_thinking_level,  # Use global Gemini thinking level
                    "chunk_size": config.cleaning_chunk_size,
                    "overlap_pct": config.cleaning_overlap_pct,
                    "extract_entities": config.cleaning_extract_entities,
                    "base_url": config.ollama_base_url,
                    "api_key": config.openai_api_key,
                    "gemini_api_key": config.gemini_api_key,
                    "anthropic_api_key": config.anthropic_api_key,
                }

            # Convert language for provider (Google needs BCP-47 format)
            transcribe_language = language
            if config.transcription_provider.lower() == "google":
                locale_map = {"en": "en-US", "hr": "hr-HR", "de": "de-DE", "es": "es-ES", "fr": "fr-FR", "it": "it-IT"}
                transcribe_language = locale_map.get(language, f"{language}-{language.upper()}")

            transcript_data = transcriber.transcribe_audio(
                transcription_audio_path,
                output,
                options=TranscribeOptions(language=transcribe_language),
            )

            # Cleanup temporary files
            if preprocessed_audio_path and preprocessed_audio_path != audio_path:
                preprocessor.cleanup_preprocessed_file(preprocessed_audio_path)

            if transcript_data:
                click.echo("✅ Transcription complete!")
                click.echo(f"📄 Transcript saved to: {output}")
            elif getattr(ctx.obj, "using_webhook_mode", False):
                # In webhook mode, None means "submitted, waiting for callback"
                click.echo("📤 Transcription submitted - waiting for webhook callback")
                click.echo(f"   Transcript will be saved to: {output}")
            else:
                click.echo("❌ Transcription failed", err=True)
                ctx.exit(1)

        except Exception as e:
            click.echo(f"❌ Error during transcription: {e}", err=True)
            # Cleanup on error
            if (
                "preprocessed_audio_path" in locals()
                and preprocessed_audio_path
                and preprocessed_audio_path != audio_path
            ):
                preprocessor.cleanup_preprocessed_file(preprocessed_audio_path)
            ctx.exit(1)
        return

    # Helper to cleanup webhook server on any exit path
    def cleanup_webhook_server():
        if hasattr(ctx.obj, "webhook_server_context") and ctx.obj.webhook_server_context:
            ctx.obj.webhook_server_context.__exit__(None, None, None)
            # Only show "stopped" message if we actually started a server (not if we detected an existing one)
            if getattr(ctx.obj, "started_webhook_server", False):
                click.echo("🌐 Webhook server stopped")

    # Mode 2: Batch transcription of downloaded episodes
    # Use shared services from context
    podcast_service = ctx.obj.podcast_service
    feed_manager = ctx.obj.feed_manager

    # Validate episode_id requires podcast_id
    if episode_id and not podcast_id:
        click.echo("❌ --episode-id requires --podcast-id", err=True)
        cleanup_webhook_server()
        ctx.exit(1)

    click.echo("🔍 Looking for episodes to transcribe...")

    # Get episodes that need transcription (sorted by pub_date, newest first)
    episodes_to_transcribe = feed_manager.get_downloaded_episodes(str(config.storage_path))

    if not episodes_to_transcribe:
        click.echo("✓ No episodes found that need transcription")
        cleanup_webhook_server()
        return

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            cleanup_webhook_server()
            ctx.exit(1)

        episodes_to_transcribe = [(p, ep) for p, ep in episodes_to_transcribe if str(p.rss_url) == str(podcast.rss_url)]

        if not episodes_to_transcribe:
            click.echo(f"✓ No episodes need transcription for podcast: {podcast.title}")
            cleanup_webhook_server()
            return

        # Filter by episode_id if specified
        if episode_id:
            target_episode = podcast_service.get_episode(podcast_id, episode_id)
            if not target_episode:
                click.echo(f"❌ Episode not found: {episode_id}", err=True)
                cleanup_webhook_server()
                ctx.exit(1)

            # Filter to only the specific episode
            episodes_to_transcribe = [
                (p, ep) for p, ep in episodes_to_transcribe if ep.external_id == target_episode.external_id
            ]

            if not episodes_to_transcribe:
                click.echo(f"✓ Episode already transcribed: {target_episode.title}")
                cleanup_webhook_server()
                return

    # Apply max_episodes limit (simple slice on sorted list)
    if max_episodes:
        episodes_to_transcribe = episodes_to_transcribe[:max_episodes]

    # Count total episodes
    total_count = len(episodes_to_transcribe)
    click.echo(f"📝 Found {total_count} episode(s) to transcribe")

    if dry_run:
        current_podcast = None
        for podcast, episode in episodes_to_transcribe:
            # Show podcast header when switching podcasts
            if current_podcast != podcast.title:
                click.echo(f"\n📻 {podcast.title}")
                current_podcast = podcast.title
            duration_str = f" [{format_duration(episode.duration)}]" if episode.duration else ""
            click.echo(
                f"  • {episode.title}{duration_str} ({episode.pub_date.strftime('%Y-%m-%d') if episode.pub_date else 'no date'})"
            )
        click.echo("\n(Run without --dry-run to actually transcribe)")
        cleanup_webhook_server()
        return

    # Transcribe episodes
    transcribed_count = 0
    total_audio_seconds = 0  # Track total audio duration for speed calculation
    start_time = time.time()

    # Progress bar wrapper (episodes already flat and sorted)
    with click.progressbar(
        episodes_to_transcribe,
        label="Transcribing",
        show_pos=True,  # Show "X/Y" counter
        show_eta=True,  # Show estimated time
        file=sys.stderr,  # Use stderr (consistent with logging)
        item_show_func=lambda x: None,  # Disable default item display
    ) as bar:
        current_podcast = None
        for podcast, episode in bar:
            # Show podcast header when switching podcasts
            if current_podcast != podcast.title:
                click.echo(f"\n📻 {podcast.title}")
                click.echo("─" * 50)
                current_podcast = podcast.title

            duration_str = f" [{format_duration(episode.duration)}]" if episode.duration else ""
            click.echo(f"\n🎧 {episode.title}{duration_str}")

            try:
                # Only use downsampled audio - fail if not available
                if not episode.downsampled_audio_path:
                    click.echo("❌ No downsampled audio available for this episode")
                    click.echo("   Run 'thestill download' again to generate downsampled audio")
                    continue

                audio_file = config.path_manager.downsampled_audio_file(episode.downsampled_audio_path)

                # Verify downsampled audio exists before transcription
                try:
                    config.path_manager.require_file_exists(audio_file, "Downsampled audio file not found")
                except FileNotFoundError:
                    click.echo(f"❌ Downsampled audio file not found: {episode.downsampled_audio_path}")
                    continue

                # Use downsampled audio directly (no further preprocessing needed)
                transcription_audio_path = str(audio_file)

                # Determine output path using podcast subdirectory structure
                # Downsampled audio path is in format: pod-slug/episode-slug_hash.wav
                # Extract podcast slug from the path
                path_parts = Path(episode.downsampled_audio_path).parts
                if len(path_parts) >= 2:
                    # Has subdirectory structure: pod-slug/filename.wav
                    podcast_subdir = path_parts[0]
                else:
                    # Fallback for legacy flat structure
                    podcast_subdir = podcast.slug

                # Create podcast subdirectory for raw transcripts
                transcript_dir = config.path_manager.raw_transcripts_dir() / podcast_subdir
                transcript_dir.mkdir(parents=True, exist_ok=True)

                # Filename format: episode-slug_hash_transcript.json
                transcript_filename = f"{audio_file.stem}_transcript.json"
                output = str(transcript_dir / transcript_filename)

                # Database stores relative path: pod-slug/episode-slug_hash_transcript.json
                output_db_path = f"{podcast_subdir}/{transcript_filename}"

                # Transcribe
                click.echo("📝 Transcribing...")

                # Prepare cleaning config if enabled (legacy cleaning during transcription)
                cleaning_config = None
                if config.enable_transcript_cleaning:
                    cleaning_config = {
                        "provider": config.cleaning_provider,
                        "model": config.cleaning_model,
                        "thinking_level": config.gemini_thinking_level,  # Use global Gemini thinking level
                        "chunk_size": config.cleaning_chunk_size,
                        "overlap_pct": config.cleaning_overlap_pct,
                        "extract_entities": config.cleaning_extract_entities,
                        "base_url": config.ollama_base_url,
                        "api_key": config.openai_api_key,
                        "gemini_api_key": config.gemini_api_key,
                        "anthropic_api_key": config.anthropic_api_key,
                    }

                # Convert language for provider (Google needs BCP-47 format)
                episode_language = podcast.language
                if config.transcription_provider.lower() == "google":
                    locale_map = {
                        "en": "en-US",
                        "hr": "hr-HR",
                        "de": "de-DE",
                        "es": "es-ES",
                        "fr": "fr-FR",
                        "it": "it-IT",
                    }
                    episode_language = locale_map.get(
                        podcast.language, f"{podcast.language}-{podcast.language.upper()}"
                    )

                # Pass episode context for Google Cloud operation persistence
                # This allows resuming transcriptions if the app is restarted
                transcript_data = transcriber.transcribe_audio(
                    transcription_audio_path,
                    output,
                    options=TranscribeOptions(
                        language=episode_language,
                        episode_id=episode.id,
                        podcast_slug=podcast.slug,
                        episode_slug=episode.slug,
                    ),
                )

                if transcript_data:
                    # Mark episode as having transcript
                    # Clear clean_transcript_path and summary_path since underlying data changed
                    feed_manager.mark_episode_processed(
                        str(podcast.rss_url),
                        episode.external_id,
                        raw_transcript_path=output_db_path,
                        clean_transcript_path="",  # Clear - needs re-cleaning
                        summary_path="",  # Clear - needs re-summarizing
                    )
                    transcribed_count += 1

                    # Track audio duration for speed calculation
                    if episode.duration:
                        total_audio_seconds += episode.duration

                    click.echo("✅ Transcription complete!")

                    # Auto-cleanup: delete downsampled audio file after successful transcription
                    if config.delete_audio_after_processing and episode.downsampled_audio_path:
                        if preprocessor.delete_downsampled_audio(
                            episode.downsampled_audio_path,
                            str(config.path_manager.downsampled_audio_dir()),
                        ):
                            # Clear the path in database since file no longer exists
                            feed_manager.clear_episode_downsampled_audio_path(str(podcast.rss_url), episode.external_id)
                            click.echo("🗑️  Cleaned up downsampled audio file")
                elif getattr(ctx.obj, "using_webhook_mode", False):
                    # In webhook mode, None means "submitted, waiting for callback"
                    # The webhook handler will save the transcript and update the database
                    click.echo("📤 Transcription submitted - waiting for webhook callback")
                    transcribed_count += 1  # Count as submitted (will complete async)
                else:
                    click.echo("❌ Transcription failed")

            except Exception as e:
                click.echo(f"❌ Error during transcription: {e}")
                import traceback

                traceback.print_exc()
                continue

    total_time = time.time() - start_time

    # Show completion message based on mode
    if getattr(ctx.obj, "using_webhook_mode", False):
        click.echo("\n📤 Transcription submissions complete!")
        click.echo(f"✓ {transcribed_count} episode(s) submitted in {format_duration(total_time)} ({total_time:.0f}s)")
        click.echo("")

        # Wait for all webhook callbacks to complete
        from thestill.webhook import get_tracker

        tracker = get_tracker()

        if tracker.pending_count > 0:
            click.echo(f"   Waiting for {tracker.pending_count} webhook callback(s)...")
            click.echo("   (Press Ctrl+C to exit early)")
            click.echo("")

            try:
                # Wait for all callbacks with progress updates
                while not tracker.is_all_done:
                    remaining = tracker.pending_count
                    if remaining > 0:
                        # Show progress every second
                        if tracker.wait_for_all(timeout=1.0):
                            break
                    else:
                        break

                # All done!
                click.echo("🎉 All webhook callbacks received!")
                click.echo(f"✓ {tracker.completed_count} transcript(s) delivered via webhook")
            except KeyboardInterrupt:
                click.echo("\n")
                remaining = tracker.pending_count
                if remaining > 0:
                    click.echo(f"⚠️  Exiting with {remaining} pending callback(s)")
                    click.echo("   Run 'thestill server' to continue receiving callbacks")
        else:
            click.echo("   No pending callbacks to wait for.")
    else:
        click.echo("\n🎉 Transcription complete!")
        click.echo(f"✓ {transcribed_count} episode(s) transcribed in {format_duration(total_time)} ({total_time:.0f}s)")

        # Show speed statistics if we have audio duration data
        if total_audio_seconds > 0:
            click.echo(
                f"  Audio duration: {format_duration(total_audio_seconds)} | Speed: {format_speed_stats(total_time, total_audio_seconds)}"
            )

    # Cleanup background webhook server if started
    cleanup_webhook_server()


@main.command()
@click.argument("transcript_path", type=click.Path(exists=True), required=False)
@click.option("--output", "-o", help="Output path (defaults to data/summaries/<filename>_summary.md)")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be summarized")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to summarize (default: all)")
@click.option("--force", "-f", is_flag=True, help="Re-summarize even if summary exists")
@click.pass_context
@require_config
@log_command
def summarize(ctx, transcript_path, output, dry_run, max_episodes, force):
    """Summarize cleaned transcripts with comprehensive analysis.

    If TRANSCRIPT_PATH is provided, summarizes that specific file.
    Otherwise, finds the next cleaned transcript(s) without a summary.

    Produces executive summary, notable quotes, content angles, social snippets,
    resource check, and critical analysis.
    """
    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager

    # Create LLM provider
    try:
        llm_provider = create_llm_provider_from_config(config)
    except Exception as e:
        click.echo(f"Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    summarizer = TranscriptSummarizer(llm_provider, console=ctx.obj.console)

    # If transcript_path provided, summarize that specific file
    if transcript_path:
        transcript_path_obj = Path(transcript_path).resolve()
        with open(transcript_path_obj, "r", encoding="utf-8") as f:
            transcript_text = f.read()

        # Try to look up episode metadata from the database
        metadata = None
        podcast_slug = None
        clean_transcripts_dir = path_manager.clean_transcripts_dir().resolve()
        try:
            relative_path = transcript_path_obj.relative_to(clean_transcripts_dir)
            # If file is in a podcast subfolder, we can look up metadata
            if len(relative_path.parts) > 1:
                podcast_slug = relative_path.parts[0]
                # Extract episode slug from filename (format: episode-slug_hash_cleaned.md)
                filename_stem = transcript_path_obj.stem
                if filename_stem.endswith("_cleaned"):
                    filename_stem = filename_stem[: -len("_cleaned")]
                # filename_stem is now: episode-slug_hash
                # Extract episode slug (everything before the last underscore)
                parts = filename_stem.rsplit("_", 1)
                episode_slug = parts[0] if len(parts) > 0 else filename_stem

                # Look up podcast and episode from database
                podcasts = feed_manager.list_podcasts()
                for podcast in podcasts:
                    if podcast.slug == podcast_slug:
                        for episode in podcast.episodes:
                            if episode.slug == episode_slug:
                                metadata = EpisodeMetadata(
                                    title=episode.title,
                                    pub_date=episode.pub_date,
                                    duration_seconds=episode.duration,
                                    podcast_title=podcast.title,
                                )
                                break
                        break
        except ValueError:
            # Path is not under clean_transcripts_dir
            pass

        if output:
            output_path = Path(output)
        else:
            # Build output path with podcast subdirectory if available
            if podcast_slug:
                summary_filename = f"{transcript_path_obj.stem}_summary.md"
                output_path = path_manager.summaries_dir() / podcast_slug / summary_filename
            else:
                output_path = path_manager.summary_file(f"{transcript_path_obj.stem}_summary.md")

        click.echo(f"Summarizing transcript with {llm_provider.get_model_name()}...")
        try:
            summarizer.summarize(transcript_text, output_path, metadata=metadata)
            click.echo("Summarization complete!")
            click.echo(f"Output saved to: {output_path}")
        except Exception as e:
            click.echo(f"Error during summarization: {e}", err=True)
            ctx.exit(1)
        return

    # Find cleaned transcripts to summarize
    click.echo("🔍 Looking for cleaned transcripts to summarize...")

    podcasts = feed_manager.list_podcasts()
    transcripts_to_summarize = []

    for podcast in podcasts:
        for episode in podcast.episodes:
            if episode.clean_transcript_path:
                clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
                if not clean_path.exists():
                    continue

                # Check if already summarized (unless force)
                if not force and episode.summary_path:
                    summary_path = path_manager.summary_file(episode.summary_path)
                    if summary_path.exists():
                        continue

                transcripts_to_summarize.append((podcast, episode, clean_path))

    if not transcripts_to_summarize:
        click.echo("✓ No cleaned transcripts found to summarize")
        return

    # Apply max_episodes limit if specified, otherwise process all
    if max_episodes:
        transcripts_to_process = transcripts_to_summarize[:max_episodes]
    else:
        transcripts_to_process = transcripts_to_summarize

    total_transcripts = len(transcripts_to_process)
    click.echo(f"📄 Found {len(transcripts_to_summarize)} transcripts. Processing {total_transcripts} episodes")

    if dry_run:
        for podcast, episode, _ in transcripts_to_process:
            click.echo(f"  • {podcast.title}: {episode.title}")
        click.echo("\n(Run without --dry-run to process)")
        return

    total_processed = 0
    start_time = time.time()

    for podcast, episode, clean_path in transcripts_to_process:
        click.echo(f"\n📻 {podcast.title}")
        click.echo(f"🎧 {episode.title}")
        click.echo("─" * 50)

        try:
            with open(clean_path, "r", encoding="utf-8") as f:
                transcript_text = f.read()

            # Generate output path with podcast subdirectory
            # Use episode slug and hash from clean transcript name
            base_name = clean_path.stem
            if base_name.endswith("_cleaned"):
                base_name = base_name[: -len("_cleaned")]

            # Extract episode_slug_hash from base_name (format: podcast-slug_episode-slug_hash)
            # We want just episode-slug_hash for the filename
            parts = base_name.split("_")
            if len(parts) >= 3:
                # Last part is hash, everything between first and last is episode slug
                episode_slug_hash = "_".join(parts[1:])  # episode-slug_hash
            else:
                episode_slug_hash = base_name

            # Create podcast subdirectory
            podcast_subdir = path_manager.summaries_dir() / podcast.slug
            podcast_subdir.mkdir(parents=True, exist_ok=True)

            summary_filename = f"{episode_slug_hash}_summary.md"
            output_path = podcast_subdir / summary_filename

            # Database stores relative path: {podcast_slug}/{filename}
            summary_db_path = f"{podcast.slug}/{summary_filename}"

            # Create metadata for accurate summary
            metadata = EpisodeMetadata(
                title=episode.title,
                pub_date=episode.pub_date,
                duration_seconds=episode.duration,
                podcast_title=podcast.title,
            )

            click.echo(f"Summarizing with {llm_provider.get_model_name()}...")
            summarizer.summarize(transcript_text, output_path, metadata=metadata)

            # Update feed manager
            feed_manager.mark_episode_processed(
                str(podcast.rss_url),
                episode.external_id,
                summary_path=summary_db_path,
            )

            click.echo(f"✓ Saved: {output_path}")
            total_processed += 1

        except Exception as e:
            click.echo(f"❌ Error summarizing: {e}", err=True)
            import traceback

            traceback.print_exc()

    total_time = time.time() - start_time
    click.echo("\n🎉 Summarization complete!")
    click.echo(f"✓ {total_processed} episode(s) summarized in {total_time:.1f} seconds")


@main.command()
@click.option("--since", default=None, help="Time window for episodes (e.g., 7d, 24h, 2w). Default: config value")
@click.option("--max-episodes", "-m", type=int, default=None, help="Maximum episodes to process. Default: config value")
@click.option("--no-limit", is_flag=True, help="Process all matching episodes (no limit)")
@click.option("--dry-run", "-d", is_flag=True, help="Preview what would be processed without actually processing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt for large batches")
@click.option("--no-refresh", is_flag=True, help="Skip feed refresh (use existing discovered episodes)")
@click.option("--podcast-id", help="Filter to specific podcast (index, URL, or UUID)")
@click.option("--output", "-o", type=click.Path(), help="Custom output path for digest file")
@click.option("--ready-only", is_flag=True, help="Only include already-summarized episodes (skip processing)")
@click.option("--exclude-digested", is_flag=True, help="Exclude episodes already included in a previous digest")
@click.option(
    "--async", "async_mode", is_flag=True, help="Queue processing and return immediately (requires worker process)"
)
@click.pass_context
@require_config
@log_command
def digest(
    ctx,
    since,
    max_episodes,
    no_limit,
    dry_run,
    yes,
    no_refresh,
    podcast_id,
    output,
    ready_only,
    exclude_digested,
    async_mode,
):
    """Process new episodes and generate a morning briefing digest.

    Runs the full pipeline (refresh -> download -> downsample -> transcribe ->
    clean -> summarize) on unprocessed episodes and generates a consolidated
    markdown digest.

    Use --ready-only to skip processing and generate a digest from already-summarized
    episodes only. This is useful for quick digest generation without waiting for
    transcription and summarization.

    Use --async to queue episodes for background processing and return immediately.
    Check progress with 'thestill digest-status <digest-id>'. Note: async mode
    requires a separate worker process to be running ('thestill worker').

    By default, processes up to 10 episodes from the last 7 days. Use --no-limit
    to process all matching episodes, or adjust with --since and --max-episodes.

    Exit codes:
      0 - All episodes processed successfully (or async queued successfully)
      1 - Some episodes failed (partial success)
      2 - Complete failure or configuration error
    """
    from .core.queue_manager import QueueManager, TaskStage, TaskStatus

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository
    podcast_service = ctx.obj.podcast_service

    # Parse --since option or use config default
    if since:
        try:
            since_days = parse_time_window(since)
        except ValueError as e:
            click.echo(f"❌ Invalid --since value: {e}", err=True)
            ctx.exit(2)
    else:
        since_days = config.digest_default_since_days

    # Determine max_episodes
    if no_limit:
        effective_max_episodes = 1000  # Large number effectively meaning no limit
    elif max_episodes is not None:
        effective_max_episodes = max_episodes
    else:
        effective_max_episodes = config.digest_default_max_episodes

    # Resolve podcast_id to UUID if provided
    resolved_podcast_id = None
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(2)
        resolved_podcast_id = podcast.id
        click.echo(f"📻 Filtering to podcast: {podcast.title}")

    # Step 1: Optionally refresh feeds
    if not no_refresh and not dry_run:
        click.echo("🔍 Refreshing feeds to discover new episodes...")
        refresh_service = RefreshService(ctx.obj.feed_manager, podcast_service)
        try:
            result = refresh_service.refresh(
                podcast_id=resolved_podcast_id,
                max_episodes=None,
                dry_run=False,
            )
            if result.total_episodes > 0:
                click.echo(f"📡 Discovered {result.total_episodes} new episode(s)")
            else:
                click.echo("✓ No new episodes discovered")
        except Exception as e:
            click.echo(f"⚠️  Feed refresh failed: {e}", err=True)
            # Continue anyway - we can still process existing episodes

    # Step 2: Select episodes for processing
    mode_label = "summarized" if ready_only else "unprocessed"
    click.echo(f"\n🔍 Selecting {mode_label} episodes from last {since_days} days (max: {effective_max_episodes})...")

    # Use digest_repository from context (needed for exclude_digested filtering and persistence)
    digest_repository = ctx.obj.digest_repository

    # SqlitePodcastRepository implements EpisodeRepository interface
    # Pass digest_repository if exclude_digested is enabled
    selector = DigestEpisodeSelector(
        repository,
        digest_repository if exclude_digested else None,
    )

    criteria = DigestSelectionCriteria(
        since_days=since_days,
        max_episodes=effective_max_episodes,
        podcast_id=resolved_podcast_id,
        ready_only=ready_only,
        exclude_digested=exclude_digested,
    )

    selection = selector.select(criteria)

    if not selection.episodes:
        if ready_only:
            click.echo("✓ No summarized episodes found")
            click.echo("💡 Run without --ready-only to process episodes first")
        else:
            click.echo("✓ No episodes need processing")
            click.echo("💡 All episodes within the time window are already summarized")
        ctx.exit(0)

    # Show selection summary
    click.echo(f"\n📋 Found {selection.total_matching} episode(s) matching criteria")
    if selection.total_matching > len(selection.episodes):
        click.echo(f"   (showing first {len(selection.episodes)} due to --max-episodes limit)")

    # Group episodes by podcast for display
    episodes_by_podcast = {}
    for podcast, episode in selection.episodes:
        if podcast.title not in episodes_by_podcast:
            episodes_by_podcast[podcast.title] = []
        episodes_by_podcast[podcast.title].append(episode)

    for podcast_title, episodes in episodes_by_podcast.items():
        click.echo(f"\n📻 {podcast_title}")
        for episode in episodes:
            if episode.state == EpisodeState.SUMMARIZED:
                state_icon = "●"  # Completed/ready
            elif episode.state.value == "discovered":
                state_icon = "○"  # Not started
            else:
                state_icon = "◐"  # In progress
            click.echo(f"  {state_icon} {episode.title} [{episode.state.value}]")

    # Dry run - stop here
    if dry_run:
        click.echo("\n(Run without --dry-run to process these episodes)")
        ctx.exit(0)

    # Async mode conflicts with ready-only
    if async_mode and ready_only:
        click.echo("❌ --async and --ready-only cannot be used together", err=True)
        click.echo("   --ready-only generates immediately from summarized episodes")
        click.echo("   --async queues episodes for background processing")
        ctx.exit(2)

    # Ready-only mode: skip processing, generate digest directly
    if ready_only:
        click.echo("\n📝 Generating digest from summarized episodes...")
        start_time = time.time()

        # All selected episodes are already summarized
        successful_episodes = selection.episodes
        failed_episodes = []
        processing_time = time.time() - start_time

    else:
        # Confirmation prompt for large batches (skip for async mode)
        if len(selection.episodes) > 10 and not yes and not async_mode:
            click.echo(f"\n⚠️  About to process {len(selection.episodes)} episodes.")
            if not click.confirm("Do you want to continue?"):
                click.echo("Aborted.")
                ctx.exit(0)

        # Step 3: Process episodes through the pipeline
        queue_manager = QueueManager(str(config.database_path))
        batch_service = BatchQueueService(
            queue_manager,
            transcription_provider=config.transcription_provider,
        )

        # Async mode: queue and return immediately
        if async_mode:
            click.echo("\n🚀 Queueing episodes for background processing...")

            # Create pending digest record first
            auth_service = ctx.obj.auth_service
            default_user = auth_service.get_or_create_default_user()

            digest_model = Digest(
                user_id=default_user.id,
                period_start=criteria.date_from,
                period_end=datetime.now(timezone.utc),
                episode_ids=[ep.id for _, ep in selection.episodes],
                episodes_total=len(selection.episodes),
                status=DigestStatus.PENDING,
            )
            digest_repository.save(digest_model)

            # Queue episodes without waiting
            result = batch_service.queue_episodes(
                selection.episodes,
                wait=False,
            )

            # Update digest status to in_progress
            digest_model.mark_in_progress()
            digest_repository.save(digest_model)

            click.echo(f"\n✓ Digest created: {digest_model.id}")
            click.echo(f"   Queued: {result.queued_count} episode(s)")
            click.echo(f"   Skipped: {result.skipped_count}")
            click.echo(f"\n💡 Check progress with: thestill digest-status {digest_model.id}")
            ctx.exit(0)

        # Sync mode: queue and wait for completion
        click.echo("\n🚀 Starting batch processing...")
        start_time = time.time()

        # Progress callback for CLI output
        current_episode_idx = [0]  # Use list for mutable closure
        last_reported_stage = [None]
        last_episode_id = [None]

        def progress_callback(queued_episode, status, stage):
            # Track episode changes to update the counter
            if queued_episode.episode.id != last_episode_id[0]:
                if last_episode_id[0] is not None:
                    current_episode_idx[0] += 1
                last_episode_id[0] = queued_episode.episode.id
                last_reported_stage[0] = None  # Reset stage tracking for new episode

            episode_title = queued_episode.episode.title[:50]
            if stage != last_reported_stage[0]:
                stage_name = stage.value if stage else "unknown"
                status_name = status.value if status else "unknown"
                click.echo(
                    f"  [{current_episode_idx[0]+1}/{len(selection.episodes)}] {episode_title}... {stage_name} ({status_name})"
                )
                last_reported_stage[0] = stage

        # Queue and wait for completion
        result = batch_service.queue_episodes(
            selection.episodes,
            wait=True,
            progress_callback=progress_callback,
        )

        processing_time = time.time() - start_time

        # Summary of processing
        click.echo(f"\n📊 Processing Summary:")
        click.echo(f"   Queued: {result.queued_count}")
        click.echo(f"   Skipped: {result.skipped_count}")
        click.echo(f"   Successful: {result.successful_count}")
        click.echo(f"   Failed: {result.failed_count}")
        if result.was_interrupted:
            click.echo("   ⚠️  Processing was interrupted")

        # Step 4: Generate digest
        click.echo("\n📝 Generating digest...")

        # Collect successful episodes for digest
        successful_episodes = []
        failed_episodes = []

        for queued_episode in result.queued:
            if (
                queued_episode.final_status == TaskStatus.COMPLETED
                and queued_episode.final_stage == TaskStage.SUMMARIZE
            ):
                successful_episodes.append((queued_episode.podcast, queued_episode.episode))
            elif queued_episode.final_status in (TaskStatus.FAILED, TaskStatus.DEAD):
                failed_episodes.append(
                    (queued_episode.podcast, queued_episode.episode, queued_episode.error_message or "Unknown error")
                )

        # Add skipped episodes that were already summarized to successful list
        # (They should be included in the digest)
        for podcast, episode, reason in result.skipped:
            if reason == "already summarized":
                successful_episodes.append((podcast, episode))

    generator = DigestGenerator(path_manager)
    digest_content = generator.generate(
        episodes=successful_episodes,
        processing_time_seconds=processing_time,
        failures=failed_episodes,
    )

    # Determine output path and stored file_path
    # Always store just the filename for consistency and API compatibility
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    digest_filename = f"digest_{timestamp}.md"

    if output:
        output_path = Path(output)
        # Store just the filename - API access only works for files in default directory
        stored_file_path = digest_filename
    else:
        output_path = path_manager.digest_file(digest_filename)
        stored_file_path = digest_filename

    generator.write(digest_content, output_path)

    # Persist digest to database
    auth_service = ctx.obj.auth_service
    default_user = auth_service.get_or_create_default_user()

    # Create digest model with processing results
    digest_model = Digest(
        user_id=default_user.id,
        period_start=criteria.date_from,
        period_end=datetime.now(timezone.utc),
        episode_ids=[ep.id for _, ep in successful_episodes],
        episodes_total=digest_content.stats.total_episodes,
    )

    # Mark as completed with results
    digest_model.mark_completed(
        file_path=stored_file_path,
        episodes_completed=digest_content.stats.successful_episodes,
        episodes_failed=digest_content.stats.failed_episodes,
        processing_time_seconds=processing_time,
    )

    # Save to database
    digest_repository.save(digest_model)

    # Final summary
    click.echo(f"\n🎉 Digest generated!")
    click.echo(f"   Output: {output_path}")
    click.echo(f"   Episodes: {digest_content.stats.successful_episodes}/{digest_content.stats.total_episodes}")
    click.echo(f"   Processing time: {format_duration(int(processing_time))}")

    # Determine exit code
    if ready_only:
        # Ready-only mode: success if we have any episodes
        ctx.exit(0 if successful_episodes else 2)
    elif result.failed_count == 0 and not result.was_interrupted:
        ctx.exit(0)  # All success
    elif result.successful_count > 0:
        ctx.exit(1)  # Partial success
    else:
        ctx.exit(2)  # Complete failure


@main.command("digest-status")
@click.argument("digest_id", required=False)
@click.option("--list", "-l", "list_all", is_flag=True, help="List all digests")
@click.option("--limit", type=int, default=10, help="Maximum digests to list (default: 10)")
@click.option("--finalize", "-f", is_flag=True, help="Finalize an async digest (generate output file)")
@click.pass_context
@require_config
@log_command
def digest_status(ctx, digest_id, list_all, limit, finalize):
    """Check status of digest generation or list all digests.

    Without arguments, shows the status of the most recent digest.
    With DIGEST_ID, shows status of that specific digest.
    With --list, shows all digests.
    With --finalize, generates the digest file for a completed async digest.

    Examples:
      thestill digest-status                    # Show latest digest status
      thestill digest-status abc123             # Show specific digest
      thestill digest-status --list             # List all digests
      thestill digest-status abc123 --finalize  # Generate file for async digest
    """
    from .core.queue_manager import QueueManager, TaskStage, TaskStatus

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    digest_repository = ctx.obj.digest_repository
    repository = ctx.obj.repository

    # List all digests mode
    if list_all:
        digests = digest_repository.get_all(limit=limit)

        if not digests:
            click.echo("No digests found.")
            ctx.exit(0)

        click.echo(f"📋 Digests (showing {len(digests)}):\n")
        for d in digests:
            status_icon = {
                DigestStatus.PENDING: "⏳",
                DigestStatus.IN_PROGRESS: "🔄",
                DigestStatus.COMPLETED: "✓",
                DigestStatus.PARTIAL: "⚠️",
                DigestStatus.FAILED: "❌",
            }.get(d.status, "?")

            click.echo(f"{status_icon} {d.id[:8]}...")
            click.echo(f"   Created: {d.created_at.strftime('%Y-%m-%d %H:%M')}")
            click.echo(f"   Status: {d.status.value}")
            click.echo(f"   Episodes: {d.episodes_completed}/{d.episodes_total}")
            if d.file_path:
                click.echo(f"   Output: {d.file_path}")
            click.echo()
        ctx.exit(0)

    # Get specific or latest digest
    if digest_id:
        digest_model = digest_repository.get_by_id(digest_id)
        if not digest_model:
            click.echo(f"❌ Digest not found: {digest_id}", err=True)
            ctx.exit(2)
    else:
        digest_model = digest_repository.get_latest()
        if not digest_model:
            click.echo("No digests found. Run 'thestill digest' to create one.")
            ctx.exit(0)

    # Show digest status
    status_icon = {
        DigestStatus.PENDING: "⏳",
        DigestStatus.IN_PROGRESS: "🔄",
        DigestStatus.COMPLETED: "✓",
        DigestStatus.PARTIAL: "⚠️",
        DigestStatus.FAILED: "❌",
    }.get(digest_model.status, "?")

    click.echo(f"\n{status_icon} Digest: {digest_model.id}")
    click.echo(f"   Status: {digest_model.status.value}")
    click.echo(f"   Created: {digest_model.created_at.strftime('%Y-%m-%d %H:%M')}")
    click.echo(
        f"   Period: {digest_model.period_start.strftime('%Y-%m-%d')} to {digest_model.period_end.strftime('%Y-%m-%d')}"
    )

    # For in-progress digests, check task queue status
    if digest_model.status == DigestStatus.IN_PROGRESS:
        queue_manager = QueueManager(str(config.database_path))

        completed = 0
        failed = 0
        pending = 0

        for episode_id in digest_model.episode_ids:
            tasks = queue_manager.get_tasks_for_episode(episode_id)
            if tasks:
                latest_task = tasks[0]
                if latest_task.status == TaskStatus.COMPLETED and latest_task.stage == TaskStage.SUMMARIZE:
                    completed += 1
                elif latest_task.status in (TaskStatus.FAILED, TaskStatus.DEAD):
                    failed += 1
                else:
                    pending += 1
            else:
                pending += 1

        click.echo(f"\n📊 Progress:")
        click.echo(f"   Completed: {completed}/{digest_model.episodes_total}")
        click.echo(f"   Failed: {failed}")
        click.echo(f"   Pending: {pending}")

        if pending == 0:
            click.echo("\n💡 All episodes processed. Run with --finalize to generate the digest file.")

    elif digest_model.status in (DigestStatus.COMPLETED, DigestStatus.PARTIAL):
        click.echo(f"\n📊 Results:")
        click.echo(f"   Completed: {digest_model.episodes_completed}/{digest_model.episodes_total}")
        click.echo(f"   Failed: {digest_model.episodes_failed}")
        if digest_model.processing_time_seconds:
            click.echo(f"   Processing time: {format_duration(int(digest_model.processing_time_seconds))}")
        if digest_model.file_path:
            click.echo(f"   Output: {digest_model.file_path}")

    elif digest_model.status == DigestStatus.FAILED:
        click.echo(f"\n❌ Error: {digest_model.error_message or 'Unknown error'}")

    # Finalize mode: generate digest file for async digest
    if finalize:
        if digest_model.status == DigestStatus.COMPLETED and digest_model.file_path:
            click.echo("\n✓ Digest already finalized.")
            ctx.exit(0)

        if digest_model.status == DigestStatus.PENDING:
            click.echo("\n❌ Cannot finalize: digest is still pending", err=True)
            ctx.exit(1)

        # Collect episode results from queue
        click.echo("\n📝 Generating digest file...")
        queue_manager = QueueManager(str(config.database_path))

        successful_episodes = []
        failed_episodes = []
        missing_episode_ids = []

        for episode_id in digest_model.episode_ids:
            # Get episode and podcast from repository
            episode_data = repository.get_episode_by_id(episode_id)
            if not episode_data:
                missing_episode_ids.append(episode_id)
                click.echo(
                    f"⚠️  Episode not found in database: {episode_id}",
                    file=sys.stderr,
                )
                continue

            podcast, episode = episode_data

            tasks = queue_manager.get_tasks_for_episode(episode_id)
            if tasks:
                latest_task = tasks[0]
                if latest_task.status == TaskStatus.COMPLETED and latest_task.stage == TaskStage.SUMMARIZE:
                    successful_episodes.append((podcast, episode))
                elif latest_task.status in (TaskStatus.FAILED, TaskStatus.DEAD):
                    failed_episodes.append((podcast, episode, latest_task.error_message or "Unknown error"))
            else:
                # Check if episode is already summarized
                if episode.state == EpisodeState.SUMMARIZED:
                    successful_episodes.append((podcast, episode))

        if not successful_episodes and not failed_episodes:
            click.echo("❌ No processed episodes found", err=True)
            ctx.exit(1)

        # Generate digest
        generator = DigestGenerator(path_manager)
        digest_content = generator.generate(
            episodes=successful_episodes,
            processing_time_seconds=None,
            failures=failed_episodes,
        )

        # Write output (store just filename since it's in default digests directory)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        digest_filename = f"digest_{timestamp}.md"
        output_path = path_manager.digest_file(digest_filename)
        generator.write(digest_content, output_path)

        # Update digest model
        digest_model.mark_completed(
            file_path=digest_filename,
            episodes_completed=digest_content.stats.successful_episodes,
            episodes_failed=digest_content.stats.failed_episodes,
            processing_time_seconds=0,
        )
        digest_repository.save(digest_model)

        click.echo(f"\n🎉 Digest finalized!")
        click.echo(f"   Output: {output_path}")
        click.echo(f"   Episodes: {digest_content.stats.successful_episodes}/{digest_content.stats.total_episodes}")
        if missing_episode_ids:
            click.echo(
                f"   ⚠️  {len(missing_episode_ids)} episode(s) not found in database (may have been deleted)",
                file=sys.stderr,
            )


@main.command("evaluate-raw-transcript")
@click.argument("transcript_path", type=click.Path(exists=True), required=False)
@click.option("--output", "-o", help="Output path for evaluation report (standalone mode only)")
@click.option("--podcast-id", help="Evaluate transcripts from specific podcast (index, URL, or UUID)")
@click.option("--episode-id", help="Evaluate specific episode (requires --podcast-id)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to evaluate")
@click.option("--dry-run", "-d", is_flag=True, help="Preview what would be evaluated")
@click.option("--force", "-f", is_flag=True, help="Re-evaluate even if evaluation exists")
@click.pass_context
@require_config
def evaluate_raw_transcript(ctx, transcript_path, output, podcast_id, episode_id, max_episodes, dry_run, force):
    """Evaluate the quality of raw transcripts.

    If TRANSCRIPT_PATH is provided, evaluates that specific file.
    Otherwise, discovers episodes with raw transcripts and evaluates them in batch.

    Uses LLM to analyze transcript quality including accuracy, completeness,
    entity handling, and structural clarity.
    """
    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager
    podcast_service = ctx.obj.podcast_service

    # Create LLM provider
    try:
        llm_provider = create_llm_provider_from_config(config)
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    evaluator = TranscriptEvaluator(llm_provider, console=ctx.obj.console)

    # Standalone mode: evaluate a specific file
    if transcript_path:
        import json

        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_data = json.load(f)

        if not output:
            transcript_path_obj = Path(transcript_path)
            output = str(transcript_path_obj.parent / f"{transcript_path_obj.stem}_evaluation.json")

        click.echo(f"📊 Evaluating transcript quality with {llm_provider.get_model_name()}...")

        try:
            evaluation = evaluator.evaluate(transcript_data, output)
            print_evaluation_summary(evaluation, "transcript", console=ctx.obj.console)
            click.echo(f"📄 Detailed report saved to: {output}")
        except Exception as e:
            click.echo(f"❌ Error during evaluation: {e}", err=True)
            ctx.exit(1)
        return

    # Batch mode: discover and evaluate episodes with raw transcripts
    click.echo("🔍 Looking for raw transcripts to evaluate...")

    episodes_to_evaluate = feed_manager.get_episodes_with_raw_transcripts(str(config.storage_path))

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)
        episodes_to_evaluate = [(p, ep) for p, ep in episodes_to_evaluate if str(p.rss_url) == str(podcast.rss_url)]

    # Filter by episode_id if specified
    if episode_id:
        if not podcast_id:
            click.echo("❌ --episode-id requires --podcast-id", err=True)
            ctx.exit(1)
        target_episode = podcast_service.get_episode(podcast_id, episode_id)
        if not target_episode:
            click.echo(f"❌ Episode not found: {episode_id}", err=True)
            ctx.exit(1)
        episodes_to_evaluate = [
            (p, ep) for p, ep in episodes_to_evaluate if ep.external_id == target_episode.external_id
        ]

    # Filter out already-evaluated episodes (unless --force)
    if not force:
        filtered = []
        for podcast, episode in episodes_to_evaluate:
            eval_filename = f"{Path(episode.raw_transcript_path).stem}_evaluation.json"
            eval_path = path_manager.raw_transcript_evaluation_file(podcast.slug, eval_filename)
            if not eval_path.exists():
                filtered.append((podcast, episode))
        episodes_to_evaluate = filtered

    if not episodes_to_evaluate:
        click.echo("✓ No raw transcripts found to evaluate")
        return

    # Apply max_episodes limit
    if max_episodes:
        episodes_to_evaluate = episodes_to_evaluate[:max_episodes]

    total_episodes = len(episodes_to_evaluate)
    click.echo(f"📄 Found {total_episodes} transcript(s) to evaluate")

    if dry_run:
        for podcast, episode in episodes_to_evaluate:
            click.echo(f"  • {podcast.title}: {episode.title}")
        click.echo("\n(Run without --dry-run to evaluate)")
        return

    # Process episodes
    import json

    total_processed = 0
    start_time = time.time()

    current_podcast = None
    for podcast, episode in episodes_to_evaluate:
        if current_podcast != podcast.title:
            click.echo(f"\n📻 {podcast.title}")
            click.echo("─" * 50)
            current_podcast = podcast.title

        click.echo(f"\n🎧 {episode.title}")

        try:
            # Load transcript
            transcript_path_obj = path_manager.raw_transcript_file(episode.raw_transcript_path)
            with open(transcript_path_obj, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)

            # Determine output path
            eval_filename = f"{transcript_path_obj.stem}_evaluation.json"
            eval_path = path_manager.raw_transcript_evaluation_file(podcast.slug, eval_filename)
            eval_path.parent.mkdir(parents=True, exist_ok=True)

            click.echo(f"   📊 Evaluating with {llm_provider.get_model_name()}...")
            evaluation = evaluator.evaluate(transcript_data, str(eval_path))
            print_evaluation_summary(evaluation, "transcript", console=ctx.obj.console)
            click.echo(f"   ✓ Saved: {eval_path}")
            total_processed += 1

        except Exception as e:
            click.echo(f"   ❌ Error: {e}", err=True)
            import traceback

            traceback.print_exc()

    total_time = time.time() - start_time
    click.echo("\n🎉 Evaluation complete!")
    click.echo(f"✓ {total_processed} transcript(s) evaluated in {total_time:.1f} seconds")


@main.command("evaluate-clean-transcript")
@click.argument("transcript_path", type=click.Path(exists=True), required=False)
@click.option("--original", help="Path to original transcript for comparison (standalone mode only)")
@click.option("--output", "-o", help="Output path for evaluation report (standalone mode only)")
@click.option("--podcast-id", help="Evaluate transcripts from specific podcast (index, URL, or UUID)")
@click.option("--episode-id", help="Evaluate specific episode (requires --podcast-id)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to evaluate")
@click.option("--dry-run", "-d", is_flag=True, help="Preview what would be evaluated")
@click.option("--force", "-f", is_flag=True, help="Re-evaluate even if evaluation exists")
@click.pass_context
@require_config
def evaluate_clean_transcript(
    ctx, transcript_path, original, output, podcast_id, episode_id, max_episodes, dry_run, force
):
    """Evaluate the quality of clean transcripts.

    If TRANSCRIPT_PATH is provided, evaluates that specific file.
    Otherwise, discovers episodes with clean transcripts and evaluates them in batch.

    Uses LLM to analyze fidelity, formatting, readability, and enhancements.
    """
    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager
    podcast_service = ctx.obj.podcast_service

    # Create LLM provider
    try:
        llm_provider = create_llm_provider_from_config(config)
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    evaluator = PostProcessorEvaluator(llm_provider, console=ctx.obj.console)

    # Standalone mode: evaluate a specific file
    if transcript_path:
        import json

        with open(transcript_path, "r", encoding="utf-8") as f:
            if transcript_path.endswith(".json"):
                transcript_data = json.load(f)
            else:
                # If it's markdown, wrap it as content
                transcript_data = {"full_output": f.read()}

        # Load original if provided
        original_data = None
        if original:
            with open(original, "r", encoding="utf-8") as f:
                original_data = json.load(f)

        if not output:
            transcript_path_obj = Path(transcript_path)
            output = str(transcript_path_obj.parent / f"{transcript_path_obj.stem}_evaluation.json")

        click.echo(f"📊 Evaluating clean transcript quality with {llm_provider.get_model_name()}...")

        try:
            evaluation = evaluator.evaluate(transcript_data, original_data, output)
            print_evaluation_summary(evaluation, "clean-transcript", console=ctx.obj.console)
            click.echo(f"📄 Detailed report saved to: {output}")
        except Exception as e:
            click.echo(f"❌ Error during evaluation: {e}", err=True)
            ctx.exit(1)
        return

    # Batch mode: discover and evaluate episodes with clean transcripts
    click.echo("🔍 Looking for clean transcripts to evaluate...")

    episodes_to_evaluate = feed_manager.get_episodes_with_clean_transcripts(str(config.storage_path))

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)
        episodes_to_evaluate = [(p, ep) for p, ep in episodes_to_evaluate if str(p.rss_url) == str(podcast.rss_url)]

    # Filter by episode_id if specified
    if episode_id:
        if not podcast_id:
            click.echo("❌ --episode-id requires --podcast-id", err=True)
            ctx.exit(1)
        target_episode = podcast_service.get_episode(podcast_id, episode_id)
        if not target_episode:
            click.echo(f"❌ Episode not found: {episode_id}", err=True)
            ctx.exit(1)
        episodes_to_evaluate = [
            (p, ep) for p, ep in episodes_to_evaluate if ep.external_id == target_episode.external_id
        ]

    # Filter out already-evaluated episodes (unless --force)
    if not force:
        filtered = []
        for podcast, episode in episodes_to_evaluate:
            eval_filename = f"{Path(episode.clean_transcript_path).stem}_evaluation.json"
            eval_path = path_manager.clean_transcript_evaluation_file(podcast.slug, eval_filename)
            if not eval_path.exists():
                filtered.append((podcast, episode))
        episodes_to_evaluate = filtered

    if not episodes_to_evaluate:
        click.echo("✓ No clean transcripts found to evaluate")
        return

    # Apply max_episodes limit
    if max_episodes:
        episodes_to_evaluate = episodes_to_evaluate[:max_episodes]

    total_episodes = len(episodes_to_evaluate)
    click.echo(f"📄 Found {total_episodes} transcript(s) to evaluate")

    if dry_run:
        for podcast, episode in episodes_to_evaluate:
            click.echo(f"  • {podcast.title}: {episode.title}")
        click.echo("\n(Run without --dry-run to evaluate)")
        return

    # Process episodes
    import json

    total_processed = 0
    start_time = time.time()

    current_podcast = None
    for podcast, episode in episodes_to_evaluate:
        if current_podcast != podcast.title:
            click.echo(f"\n📻 {podcast.title}")
            click.echo("─" * 50)
            current_podcast = podcast.title

        click.echo(f"\n🎧 {episode.title}")

        try:
            # Load clean transcript
            clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
            with open(clean_path, "r", encoding="utf-8") as f:
                if str(clean_path).endswith(".json"):
                    transcript_data = json.load(f)
                else:
                    transcript_data = {"full_output": f.read()}

            # Load raw transcript for comparison if available
            original_data = None
            if episode.raw_transcript_path:
                raw_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
                if raw_path.exists():
                    with open(raw_path, "r", encoding="utf-8") as f:
                        original_data = json.load(f)

            # Determine output path
            eval_filename = f"{clean_path.stem}_evaluation.json"
            eval_path = path_manager.clean_transcript_evaluation_file(podcast.slug, eval_filename)
            eval_path.parent.mkdir(parents=True, exist_ok=True)

            click.echo(f"   📊 Evaluating with {llm_provider.get_model_name()}...")
            evaluation = evaluator.evaluate(transcript_data, original_data, str(eval_path))
            print_evaluation_summary(evaluation, "clean-transcript", console=ctx.obj.console)
            click.echo(f"   ✓ Saved: {eval_path}")
            total_processed += 1

        except Exception as e:
            click.echo(f"   ❌ Error: {e}", err=True)
            import traceback

            traceback.print_exc()

    total_time = time.time() - start_time
    click.echo("\n🎉 Evaluation complete!")
    click.echo(f"✓ {total_processed} transcript(s) evaluated in {total_time:.1f} seconds")


# ---------------------------------------------------------------------------
# Spec #28 — entity-layer CLI commands
# ---------------------------------------------------------------------------


def _get_or_create_cli_resolver(ctx):
    """Lazy ReFinED resolver for CLI commands.

    Same pattern as ``task_handlers._get_or_create_entity_resolver``
    but scoped to ``CLIContext`` rather than ``AppState``. Single-
    threaded by construction (CLI commands run in the main thread),
    so no lock needed here.
    """
    if ctx.obj.entity_resolver is None:
        from .core.entity_resolver import EntityResolver
        from .core.wikidata_client import WikidataClient

        ctx.obj.entity_resolver = EntityResolver(wikidata_client=WikidataClient())
    return ctx.obj.entity_resolver


@main.command("resolve-entities")
@click.option("--episode-id", help="Resolve a single episode by id")
@click.option("--podcast-id", help="Filter to one podcast (index, slug, or RSS URL)")
@click.option("--max-episodes", "-m", type=int, help="Cap the number of episodes")
@click.option("--dry-run", "-d", is_flag=True, help="Report what would resolve without writing")
@click.pass_context
@require_config
@log_command
def resolve_entities(ctx, episode_id, podcast_id, max_episodes, dry_run):
    """Resolve pending ``entity_mentions`` to Wikidata entities.

    Manual driver for the ``resolve-entities`` pipeline stage. By
    default resolves every episode that has pending mentions; use
    ``--episode-id`` for a one-shot run, ``--podcast-id`` to scope
    by podcast, ``--max-episodes`` to cap the batch.
    """
    from .core.entity_resolver import EntityResolver  # noqa: F401  (warm import)
    from .models.entities import EntityExtractionStatus

    repo = ctx.obj.entity_repository
    podcast_repo = ctx.obj.repository

    if episode_id:
        episode_ids = [episode_id]
    else:
        # Find episodes with pending mentions, optionally scoped to a
        # podcast. We grab the IDs up front so progress reporting is
        # accurate.
        sql = "SELECT DISTINCT episode_id FROM entity_mentions " "WHERE resolution_status = 'pending'"
        params: list = []
        if podcast_id:
            podcast = ctx.obj.podcast_service.get_podcast(podcast_id)
            if not podcast:
                click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
                ctx.exit(1)
            sql += " AND episode_id IN (SELECT id FROM episodes WHERE podcast_id = ?)"
            params.append(podcast.id)
        sql += " ORDER BY episode_id"
        if max_episodes:
            sql += f" LIMIT {int(max_episodes)}"
        with repo._get_connection() as conn:
            episode_ids = [r[0] for r in conn.execute(sql, params).fetchall()]

    click.echo(f"Found {len(episode_ids)} episode(s) with pending mentions")
    if dry_run:
        for eid in episode_ids:
            click.echo(f"  would resolve {eid}")
        return

    resolver = _get_or_create_cli_resolver(ctx)
    total_resolved = 0
    total_unresolvable = 0
    for eid in episode_ids:
        pending = repo.list_pending_mentions(episode_id=eid)
        if not pending:
            continue
        results = resolver.resolve(pending)
        for r in results:
            repo.upsert_entity(r.entity)
            repo.resolve_mention(
                mention_id=r.mention_id,
                entity_id=r.entity.id if r.status == "resolved" else None,
                status=r.status,
            )
            if r.status == "resolved":
                total_resolved += 1
            else:
                total_unresolvable += 1
        # Inline scoped maintenance — same as the handler.
        repo.rebuild_cooccurrences(episode_ids=[eid])
        # Mirror the handler: keep the per-episode status consistent
        # with the resolved state. Extraction set this to 'complete'
        # already — leaving it untouched matches the spec's
        # per-mention-status model.
        _ = EntityExtractionStatus  # silence unused-import; kept for future status writes
        click.echo(f"✓ {eid}: {len(results)} mentions processed")

    click.echo(
        f"\n🎉 Resolved {total_resolved} mentions, "
        f"{total_unresolvable} unresolvable, across {len(episode_ids)} episode(s)"
    )


@main.command("rebuild-cooccurrences")
@click.option("--podcast-id", help="Rebuild only pairs touching this podcast's episodes")
@click.option("--episode-id", help="Rebuild only pairs touching this single episode")
@click.option("--full", is_flag=True, help="Wipe and rebuild the entire entity_cooccurrences table")
@click.pass_context
@require_config
@log_command
def rebuild_cooccurrences(ctx, podcast_id, episode_id, full):
    """Materialise ``entity_cooccurrences`` from resolved mentions.

    Default scope is "every episode with at least one resolved
    mention" — equivalent to ``--full`` but slightly cheaper because
    the per-pair self-join runs once. ``--full`` blows away the table
    first (use after a Phase change that affects how entities are
    resolved). ``--episode-id``/``--podcast-id`` scope the rebuild
    to pairs touching specific episodes.
    """
    repo = ctx.obj.entity_repository
    if full:
        click.echo("Wiping and rebuilding entity_cooccurrences (full)…")
        rows = repo.rebuild_cooccurrences(episode_ids=None)
        click.echo(f"✓ {rows} co-occurrence pair(s) materialised")
        return

    episode_ids: list = []
    if episode_id:
        episode_ids = [episode_id]
    elif podcast_id:
        podcast = ctx.obj.podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)
        with repo._get_connection() as conn:
            episode_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM episodes WHERE podcast_id = ?",
                    (podcast.id,),
                ).fetchall()
            ]
    else:
        with repo._get_connection() as conn:
            episode_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT episode_id FROM entity_mentions " "WHERE resolution_status = 'resolved'"
                ).fetchall()
            ]

    if not episode_ids:
        click.echo("No resolved mentions to scope the rebuild from.")
        return

    rows = repo.rebuild_cooccurrences(episode_ids=episode_ids)
    click.echo(f"✓ {rows} co-occurrence pair(s) materialised across {len(episode_ids)} episode(s)")


@main.command("backfill-entity-types")
@click.option("--episode-id", help="Scope backfill to entities mentioned in this episode")
@click.option("--podcast-id", help="Scope backfill to entities mentioned in this podcast")
@click.option("--limit", type=int, help="Cap the number of entities to process")
@click.option("--dry-run", "-d", is_flag=True, help="Show reclassifications without writing")
@click.pass_context
@require_config
@log_command
def backfill_entity_types(ctx, episode_id, podcast_id, limit, dry_run):
    """Spec #28 §5.2 — fetch Wikidata P31 and re-bucket existing entities.

    Walks resolved entities with a ``wikidata_qid`` set, fetches
    ``instance of`` (P31) via the Wikidata API, and applies the same
    ``classify_entity_type`` rules the resolver runs on new resolutions.
    Persists the P31 list on each entity row so subsequent calls are
    cheap.

    When the rules pick a different bucket than what's stored, a NEW
    entity row is created at the corrected ``{type}:{slug}`` id and
    every mention pointing at the old row is repointed to the new one.
    The old row is left in place for safety — run ``merge-aliases``
    afterwards to collapse the now-empty originals.

    Scope by ``--episode-id`` or ``--podcast-id`` to test on a single
    episode before running corpus-wide. ``--dry-run`` reports the
    planned changes without writing.
    """
    from .core.entity_resolver import _build_entity_id
    from .core.entity_type_rules import classify_entity_type
    from .core.wikidata_client import WikidataClient
    from .models.entities import EntityRecord, EntityType

    repo = ctx.obj.entity_repository

    sql = (
        "SELECT DISTINCT ent.id "
        "FROM entities ent "
        "JOIN entity_mentions m ON m.entity_id = ent.id "
        "WHERE ent.wikidata_qid IS NOT NULL "
    )
    params: list = []
    if episode_id:
        sql += "AND m.episode_id = ? "
        params.append(episode_id)
    if podcast_id:
        podcast = ctx.obj.podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)
        sql += "AND m.episode_id IN (SELECT id FROM episodes WHERE podcast_id = ?) "
        params.append(podcast.id)
    sql += "ORDER BY ent.id "
    if limit:
        sql += f"LIMIT {int(limit)}"

    with repo._get_connection() as conn:
        entity_ids = [r[0] for r in conn.execute(sql, params).fetchall()]

    if not entity_ids:
        click.echo("No resolved entities with QIDs match the scope.")
        return

    click.echo(f"Backfilling P31 for {len(entity_ids)} entit(y/ies)…")
    client = WikidataClient()
    reclassified = 0
    cached = 0
    for entity_id in entity_ids:
        entity = repo.get_entity(entity_id)
        if entity is None or not entity.wikidata_qid:
            continue
        p31 = entity.wikidata_instance_of or client.fetch_p31(entity.wikidata_qid)
        if not p31:
            continue
        classified = classify_entity_type(p31, entity.type)
        if classified is None:
            classified = entity.type
        if classified == entity.type and entity.wikidata_instance_of == p31:
            continue  # nothing to change
        new_id = _build_entity_id(classified, entity.canonical_name, entity.wikidata_qid)
        click.echo(f"  {entity.id} (type={entity.type.value}) → " f"{new_id} (type={classified.value}) [P31={p31}]")
        if dry_run:
            if classified != entity.type:
                reclassified += 1
            else:
                cached += 1
            continue
        # Persist the P31 cache on the existing row regardless of
        # whether the type changed.
        updated_entity = EntityRecord(
            id=entity.id,
            type=entity.type,
            canonical_name=entity.canonical_name,
            wikidata_qid=entity.wikidata_qid,
            aliases=entity.aliases,
            description=entity.description,
            wikidata_instance_of=p31,
            created_at=entity.created_at,
        )
        repo.upsert_entity(updated_entity)
        if classified != entity.type:
            # Create the corrected entity, repoint mentions, leave the
            # original row to be swept by merge-aliases.
            new_entity = EntityRecord(
                id=new_id,
                type=classified,
                canonical_name=entity.canonical_name,
                wikidata_qid=entity.wikidata_qid,
                aliases=entity.aliases,
                description=entity.description,
                wikidata_instance_of=p31,
            )
            repo.upsert_entity(new_entity)
            moved = repo.repoint_mentions(from_entity_id=entity.id, to_entity_id=new_id)
            click.echo(f"    repointed {moved} mention(s)")
            reclassified += 1
        else:
            cached += 1

    verb = "would be" if dry_run else "were"
    click.echo(
        f"\n🎉 backfill-entity-types: {reclassified} entit(y/ies) {verb} "
        f"reclassified, {cached} P31 cache update(s)."
    )


@main.command("merge-aliases")
@click.option(
    "--levenshtein-threshold",
    default=0.1,
    type=float,
    help="Max edit-distance / max(name_len) for a fuzzy match (default 0.1)",
)
@click.option("--dry-run", "-d", is_flag=True, help="Report merges without writing")
@click.pass_context
@require_config
@log_command
def merge_aliases(ctx, levenshtein_threshold, dry_run):
    """Collapse duplicate entities (spec #28 §1.6).

    Two-step nightly job:

    1. **QID dedupe.** Any pair of entities sharing a Wikidata QID
       collapses into the keeper picked by ``find_duplicate_qid_pairs``
       (mention count > type priority > id); the loser's mentions are
       repointed and the loser deleted.
    2. **Fuzzy merge.** Within each entity type, pairs whose
       canonical-name edit distance is < ``levenshtein-threshold``
       times the longer name's length collapse the same way. The
       default of 0.1 is the spec's recommended threshold.

    Idempotent: running again is a no-op once duplicates are gone.
    Run ``thestill repair-entity-types`` first if you suspect entities
    are stored under the wrong ``type`` (spec §1.6 follow-up).
    """
    from rapidfuzz.distance import Levenshtein

    repo = ctx.obj.entity_repository

    # Step 1: QID dedupe
    qid_pairs = repo.find_duplicate_qid_pairs()
    qid_merged = 0
    for qid, keeper, loser in qid_pairs:
        if dry_run:
            click.echo(f"  [dry-run] QID {qid}: would merge {loser} → {keeper}")
            qid_merged += 1
            continue
        repo.repoint_mentions(from_entity_id=loser, to_entity_id=keeper)
        repo.delete_entity(loser)
        qid_merged += 1
    label = "would merge" if dry_run else "merge(s)"
    click.echo(f"QID dedupe: {qid_merged} {label}")

    # Step 2: fuzzy merge within each type
    fuzzy_merged = 0
    for entity_type in ("person", "company", "product", "topic"):
        entities = repo.list_entities_by_type(entity_type)
        # Pairwise within the type. ``list_entities_by_type`` returns
        # them sorted by canonical_name, which clusters near-duplicates
        # together.
        for i, ent_a in enumerate(entities):
            for ent_b in entities[i + 1 :]:
                if ent_a.canonical_name == ent_b.canonical_name:
                    continue  # exact-name dupes shouldn't exist within a type, but skip safely
                # Don't merge if both have QIDs and they differ — those
                # are genuine distinct entities (e.g. "Tim Cook" vs
                # "Tim Cooke" the photographer).
                if ent_a.wikidata_qid and ent_b.wikidata_qid and ent_a.wikidata_qid != ent_b.wikidata_qid:
                    continue
                a_name = ent_a.canonical_name
                b_name = ent_b.canonical_name
                max_len = max(len(a_name), len(b_name))
                if max_len == 0:
                    continue
                distance = Levenshtein.distance(a_name, b_name)
                if distance / max_len > levenshtein_threshold:
                    continue
                # Keeper preference: the one with a QID, then the
                # alphabetically-first id.
                if ent_b.wikidata_qid and not ent_a.wikidata_qid:
                    keeper, loser = ent_b, ent_a
                elif ent_a.id < ent_b.id:
                    keeper, loser = ent_a, ent_b
                else:
                    keeper, loser = ent_b, ent_a
                if dry_run:
                    click.echo(
                        f"  [dry-run] fuzzy {entity_type}: "
                        f"would merge {loser.id!r} ({loser.canonical_name!r}) → "
                        f"{keeper.id!r} ({keeper.canonical_name!r}) (d={distance})"
                    )
                    fuzzy_merged += 1
                    continue
                repo.repoint_mentions(from_entity_id=loser.id, to_entity_id=keeper.id)
                repo.delete_entity(loser.id)
                fuzzy_merged += 1
    click.echo(f"Fuzzy merge: {fuzzy_merged} {label}")
    click.echo(f"\n🎉 Total: {qid_merged + fuzzy_merged} {label}")


@main.command("repair-entity-types")
@click.option(
    "--min-mentions",
    default=3,
    type=int,
    help="Skip entities with fewer resolved mentions than this (default 3)",
)
@click.option(
    "--min-majority-ratio",
    default=0.8,
    type=float,
    help="Skip entities whose majority surface_label is below this ratio (default 0.8)",
)
@click.option("--dry-run", "-d", is_flag=True, help="Report repairs without writing")
@click.pass_context
@require_config
@log_command
def repair_entity_types(ctx, min_mentions, min_majority_ratio, dry_run):
    """Relabel entities whose stored type disagrees with the majority
    surface_label of their mentions (spec #28 §1.6 follow-up).

    Backfills the bug where the older QID-dedupe keeper rule sorted by
    alphabetical id, leaving entities like "Donald Trump" typed as
    ``company`` despite hundreds of person-labeled mentions. The new
    ``find_duplicate_qid_pairs`` ranking prevents recurrence; this
    command corrects the legacy rows.

    For each candidate the slug stays the same and only the type
    prefix changes. If the corrected ``id`` already exists, mentions
    are repointed onto it and the mistyped row is deleted; otherwise
    a fresh entity row is created (canonical_name, QID, aliases,
    description preserved).
    """
    from .models.entities import EntityRecord, EntityType

    repo = ctx.obj.entity_repository
    candidates = repo.find_mistyped_entities(
        min_mentions=min_mentions,
        min_majority_ratio=min_majority_ratio,
    )
    if not candidates:
        click.echo("No mistyped entities to repair.")
        return

    repaired = 0
    skipped = 0
    label = "would relabel" if dry_run else "relabeled"
    for entity_id, current_type, suggested_type, top_count, total in candidates:
        old = repo.get_entity(entity_id)
        if old is None:
            continue
        slug = entity_id.split(":", 1)[1]
        new_id = f"{suggested_type}:{slug}"

        existing_target = repo.get_entity(new_id)
        if (
            existing_target is not None
            and existing_target.wikidata_qid
            and old.wikidata_qid
            and existing_target.wikidata_qid != old.wikidata_qid
        ):
            click.echo(
                f"  [skip] {entity_id} → {new_id}: target exists with different QID "
                f"({existing_target.wikidata_qid} vs {old.wikidata_qid})"
            )
            skipped += 1
            continue

        if dry_run:
            click.echo(
                f"  [dry-run] {entity_id} → {new_id} " f"({top_count}/{total} mentions labeled {suggested_type})"
            )
            repaired += 1
            continue

        if existing_target is None:
            # ``list`` is a registered click command at module scope; spread instead.
            repo.upsert_entity(
                EntityRecord(
                    id=new_id,
                    type=EntityType(suggested_type),
                    canonical_name=old.canonical_name,
                    wikidata_qid=old.wikidata_qid,
                    aliases=[*old.aliases],
                    description=old.description,
                )
            )
        repo.repoint_mentions(from_entity_id=entity_id, to_entity_id=new_id)
        repo.delete_entity(entity_id)
        repaired += 1
        click.echo(f"  ✓ {entity_id} → {new_id} ({top_count}/{total})")

    click.echo(f"\nType repair: {repaired} {label}, {skipped} skipped")


# ---------------------------------------------------------------------------
# Spec #28 Phase 3.1 — entity-branch backfill
# ---------------------------------------------------------------------------


@main.command("rebuild-entities")
@click.option("--podcast-id", help="Restrict to one podcast (UUID or slug).")
@click.option("--since", help="Only episodes published in this window (e.g. 30d, 24h, 2w).")
@click.option("--max-episodes", "-m", type=int, help="Cap the number of episodes processed.")
@click.option("--dry-run", "-d", is_flag=True, help="Report what would be rebuilt without writing.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt for unscoped runs.")
@click.pass_context
@require_config
@log_command
def rebuild_entities(ctx, podcast_id, since, max_episodes, dry_run, yes):
    """Re-run entity extraction + resolution over a slice of the corpus.

    Use after upgrading GLiNER, extending entity types, or fixing
    extraction bugs. Wipes ``entity_mentions`` for matching episodes,
    resets ``entity_extraction_status`` to ``pending``, and enqueues an
    ``extract-entities`` task per episode — the worker re-runs the
    entity branch (extract → resolve → reindex) with normal retry/DLQ
    semantics. The user-facing pipeline state is untouched.

    Episodes without a JSON sidecar (legacy Markdown-only cleaning) are
    re-marked ``skipped_legacy`` by the handler; they're included in
    scope so the count in ``thestill status`` stays accurate.
    """
    from .core.queue_manager import QueueManager, TaskStage

    config = ctx.obj.config
    podcast_repo = ctx.obj.repository
    entity_repo = ctx.obj.entity_repository

    date_range = _date_range_from_since(since)

    if podcast_id:
        podcast = ctx.obj.podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)
        podcasts = [podcast]
    else:
        podcasts = podcast_repo.get_all()

    eligible: list = []
    for p in podcasts:
        for ep in p.episodes or []:
            if date_range is not None:
                pd = ep.pub_date
                if pd is None:
                    continue
                # Episode pub_date may be tz-naive (older rows persisted
                # without tz) or tz-aware. ``_date_range_from_since`` is
                # always tz-aware UTC; assume the same for naive rows.
                if pd.tzinfo is None:
                    pd = pd.replace(tzinfo=timezone.utc)
                if not (date_range[0] <= pd <= date_range[1]):
                    continue
            eligible.append((p, ep))

    if max_episodes:
        eligible = eligible[:max_episodes]

    click.echo(f"Eligible episodes: {len(eligible)}")
    if dry_run:
        for p, e in eligible[:20]:
            click.echo(f"  - {p.title} :: {e.title}")
        if len(eligible) > 20:
            click.echo(f"  … (+{len(eligible) - 20} more)")
        return

    if not eligible:
        click.echo("Nothing to do.")
        return

    if not podcast_id and not yes:
        # Unscoped runs touch the whole corpus — confirm interactively.
        if not click.confirm(
            f"This will wipe entity_mentions for {len(eligible)} episode(s) "
            f"and re-enqueue the entity branch. Proceed?",
            default=False,
        ):
            click.echo("Aborted.")
            return

    queue = QueueManager(str(config.database_path))
    enqueued = 0
    skipped_existing = 0
    for _p, ep in eligible:
        # Don't enqueue a duplicate if extract-entities is already pending
        # or processing — let the worker drain naturally first.
        if queue.has_pending_task(ep.id, TaskStage.EXTRACT_ENTITIES):
            skipped_existing += 1
            continue
        entity_repo.delete_mentions_for_episode(ep.id)
        podcast_repo.update_entity_extraction_status(episode_id=ep.id, status="pending")
        queue.add_task(ep.id, TaskStage.EXTRACT_ENTITIES)
        enqueued += 1

    click.echo(f"✓ rebuild-entities: {enqueued} enqueued, " f"{skipped_existing} skipped (already queued)")


# ---------------------------------------------------------------------------
# Spec #28 §1.9 — entity-layer query CLI peers (mirror of MCP §1.8 tools)
# ---------------------------------------------------------------------------


def _date_range_from_since(since: Optional[str]):
    """Convert ``--since 30d`` etc. to a closed (start, end) interval.

    Returns ``None`` when ``since`` is None, leaving downstream queries
    unscoped. Reuses ``parse_time_window`` so the syntax matches the
    existing CLI commands (``thestill digest --since 24h`` etc.).
    """
    if since is None:
        return None
    days = parse_time_window(since)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return (start, end)


def _print_citation_rows(rows, *, json_output: bool, header: Optional[str] = None) -> None:
    """Pretty-print ``CitationRow``s as a TTY table or as JSON.

    Defaults to TTY because the user is normally running interactively;
    ``--json`` switches to a single-line-per-row machine format that
    pipes cleanly into ``jq``.
    """
    if json_output:
        for row in rows:
            click.echo(json.dumps(row.model_dump(mode="json"), default=str))
        return
    if header:
        click.echo(header)
    if not rows:
        click.echo("  (no results)")
        return
    for row in rows:
        ts = row.published_at.date().isoformat() if row.published_at else "????-??-??"
        speaker = row.speaker or "?"
        quote = row.quote.replace("\n", " ").strip()[:120]
        click.echo(
            f"  {ts}  {row.podcast_title[:25]:<25}  "
            f"{row.episode_title[:35]:<35}  "
            f"@{row.start_ms // 1000}s  "
            f"{speaker[:20]:<20}  «{quote}»"
        )


@main.command("find-mentions")
@click.argument("entity")
@click.option(
    "--type",
    "entity_type",
    type=click.Choice(["person", "company", "product", "topic"]),
    help="Disambiguation hint when multiple entities share a name.",
)
@click.option("--podcast-id", help="Restrict to one podcast (UUID or slug).")
@click.option("--since", help="Time window like 30d, 6m, 24h.")
@click.option("--role", type=click.Choice(["host", "guest", "mentioned", "self"]), help="Filter by role.")
@click.option("--limit", "-l", default=50, type=int, help="Max rows.")
@click.option("--json", "json_output", is_flag=True, help="Emit one JSON row per line.")
@click.pass_context
@require_config
@log_command
def find_mentions(ctx, entity, entity_type, podcast_id, since, role, limit, json_output):
    """Find every resolved mention of an ENTITY across the corpus.

    ENTITY is matched against canonical id, canonical_name, then alias.
    Output is citation-shaped: timestamp, podcast, episode, speaker,
    quote excerpt. Same data as the MCP ``find_mentions`` tool.
    """
    from .search.citation import build_citation_rows

    repo = ctx.obj.entity_repository
    matched = repo.find_entity_by_name(entity, entity_type=entity_type)
    if matched is None:
        click.echo(f"❌ No entity matched {entity!r}", err=True)
        ctx.exit(1)

    contexts = repo.find_mentions(
        entity_id=matched.id,
        podcast_id=podcast_id,
        date_range=_date_range_from_since(since),
        role=role,
        limit=limit,
    )
    rows = build_citation_rows(contexts)
    _print_citation_rows(
        rows,
        json_output=json_output,
        header=(
            f"Resolved entity: {matched.id} ({matched.canonical_name}) "
            f"qid={matched.wikidata_qid or '—'}\n"
            f"{len(rows)} mention(s):"
        ),
    )


@main.command("quotes-by")
@click.argument("speaker")
@click.option("--topic", help="Optional topic name or entity id (intersect).")
@click.option("--podcast-id", help="Restrict to one podcast.")
@click.option("--since", help="Time window like 30d.")
@click.option("--limit", "-l", default=50, type=int, help="Max rows.")
@click.option("--json", "json_output", is_flag=True, help="Emit one JSON row per line.")
@click.pass_context
@require_config
@log_command
def quotes_by(ctx, speaker, topic, podcast_id, since, limit, json_output):
    """List resolved mentions where SPEAKER said something.

    SPEAKER is matched as a case-insensitive substring against the
    diarised label. Optional ``--topic`` intersects on episodes that
    also mention the topic. Same data as the MCP ``list_quotes_by``
    tool.
    """
    from .search.citation import build_citation_rows

    repo = ctx.obj.entity_repository
    topic_id = None
    if topic:
        topic_entity = repo.find_entity_by_name(topic)
        if topic_entity is None:
            click.echo(f"❌ No topic matched {topic!r}", err=True)
            ctx.exit(1)
        topic_id = topic_entity.id

    contexts = repo.list_mentions_by_speaker(
        speaker=speaker,
        topic_entity_id=topic_id,
        podcast_id=podcast_id,
        date_range=_date_range_from_since(since),
        limit=limit,
    )
    rows = build_citation_rows(contexts)
    header = f"{len(rows)} mention(s) by speaker matching {speaker!r}"
    if topic_id:
        header += f" intersected with {topic_id!r}"
    _print_citation_rows(rows, json_output=json_output, header=header + ":")


@main.command("search")
@click.argument("query")
@click.option(
    "--mode",
    type=click.Choice(["lexical", "semantic", "hybrid"]),
    default="hybrid",
    show_default=True,
    help="Backend strategy. Hybrid is the LLM-friendly default.",
)
@click.option("--limit", "-l", default=10, type=int, help="Max rows.")
@click.option("--podcast-id", help="Restrict to one podcast (UUID or slug).")
@click.option("--since", help="Time window like 30d, 6m, 24h. Sets date_from.")
@click.option(
    "--has-entity",
    "has_entity",
    multiple=True,
    help="Entity id (e.g. person:elon-musk) that must appear in the episode. Repeatable.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit one JSON row per line.")
@click.pass_context
@require_config
@log_command
def search(ctx, query, mode, limit, podcast_id, since, has_entity, json_output):
    """Lexical, semantic, or hybrid search over the corpus.

    Mirrors the MCP ``search_corpus`` tool and ``GET /api/search/corpus``.
    QUERY supports the same operator syntax as the MCP tool — quoted
    phrases, ``-term`` exclusion, ``speaker:foo`` for diarised filters.
    """
    from .models.entities import CitationRow, MatchType
    from .search.base import SearchFilters, SearchMode

    backend = ctx.obj.search_backend
    if backend is None:
        click.echo("❌ Search backend not initialised (install thestill[entities]?)", err=True)
        ctx.exit(1)

    # Slug → UUID convenience: ``thestill search --podcast-id my-show``
    # is friendlier than copy-pasting the UUID. Accept either by trying
    # slug-lookup first and falling back to the raw value.
    resolved_podcast_id = podcast_id
    if podcast_id:
        podcast = ctx.obj.repository.get_by_slug(podcast_id)
        if podcast is not None:
            resolved_podcast_id = podcast.id

    date_from = None
    if since:
        days = parse_time_window(since)
        date_from = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    filters = SearchFilters(
        podcast_id=resolved_podcast_id,
        date_from=date_from,
        date_to=None,
        has_entity=tuple(has_entity or ()),
    )
    hits = backend.search(query, mode=SearchMode(mode), limit=limit, filters=filters)
    rows = [
        CitationRow(
            episode_id=h.episode_id,
            podcast_id=h.podcast_id,
            podcast_title=h.podcast_title,
            episode_title=h.episode_title,
            published_at=h.published_at,
            start_ms=h.start_ms,
            end_ms=h.end_ms,
            speaker=h.speaker,
            quote=h.text[:600],
            score=h.score,
            match_type=h.match_type,
            deeplink=f"thestill://episode/{h.episode_id}?t={h.start_ms // 1000}",
            web_url=f"/episodes/{h.episode_id}?t={h.start_ms // 1000}",
        )
        for h in hits
    ]
    header = f"{len(rows)} hit(s) for {query!r} (mode={mode}):"
    _print_citation_rows(rows, json_output=json_output, header=header)


@main.group("entity")
def entity_group():
    """Inspect or manually adjust the entities table."""


@entity_group.command("get")
@click.argument("id_or_name")
@click.option(
    "--type",
    "entity_type",
    type=click.Choice(["person", "company", "product", "topic"]),
)
@click.option("--json", "json_output", is_flag=True, help="Emit a single JSON object.")
@click.pass_context
@require_config
@log_command
def entity_get(ctx, id_or_name, entity_type, json_output):
    """Show an entity record + its mention count + cooccurring entities.

    ID_OR_NAME is resolved as canonical id, then canonical name, then
    alias. Mirrors the MCP ``get_entity`` tool.
    """
    repo = ctx.obj.entity_repository
    matched = repo.find_entity_by_name(id_or_name, entity_type=entity_type)
    if matched is None:
        click.echo(f"❌ No entity matched {id_or_name!r}", err=True)
        ctx.exit(1)

    summary = repo.get_entity_summary(matched.id)
    if summary is None:
        click.echo("❌ Entity disappeared mid-query", err=True)
        ctx.exit(1)

    if json_output:
        payload = {
            "entity": {
                "id": summary["entity"].id,
                "type": summary["entity"].type.value,
                "canonical_name": summary["entity"].canonical_name,
                "wikidata_qid": summary["entity"].wikidata_qid,
                "aliases": summary["entity"].aliases,
                "description": summary["entity"].description,
            },
            "mention_count": summary["mention_count"],
            "cooccurring": [
                {
                    "id": c["entity"].id,
                    "canonical_name": c["entity"].canonical_name,
                    "episode_count": c["episode_count"],
                }
                for c in summary["cooccurring"]
            ],
            "recent_mentions": summary["recent_mentions"][0:5]
            and [
                {
                    "episode_id": ctx_.episode_id,
                    "episode_title": ctx_.episode_title,
                    "start_ms": ctx_.mention.start_ms,
                    "speaker": ctx_.mention.speaker,
                }
                for ctx_ in summary["recent_mentions"]
            ],
        }
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    e = summary["entity"]
    click.echo(f"id:             {e.id}")
    click.echo(f"type:           {e.type.value}")
    click.echo(f"canonical_name: {e.canonical_name}")
    click.echo(f"wikidata_qid:   {e.wikidata_qid or '—'}")
    if e.aliases:
        click.echo(f"aliases:        {', '.join(e.aliases)}")
    if e.description:
        click.echo(f"description:    {e.description[:120]}")
    click.echo(f"mention_count:  {summary['mention_count']}")
    if summary["cooccurring"]:
        click.echo("\nTop co-occurring:")
        for c in summary["cooccurring"][:10]:
            click.echo(f"  {c['episode_count']:>3}× {c['entity'].id}")
    if summary["recent_mentions"]:
        click.echo(f"\nRecent mentions (top {min(5, len(summary['recent_mentions']))}):")
        for mc in summary["recent_mentions"][:5]:
            ts = mc.episode_pub_date.date().isoformat() if mc.episode_pub_date else "????"
            click.echo(
                f"  {ts}  {mc.podcast_title[:25]:<25}  "
                f"@{mc.mention.start_ms // 1000}s  "
                f"{mc.mention.surface_form}"
            )


@entity_group.command("merge")
@click.argument("keeper")
@click.argument("loser")
@click.option("--dry-run", "-d", is_flag=True, help="Report without writing.")
@click.pass_context
@require_config
@log_command
def entity_merge(ctx, keeper, loser, dry_run):
    """Repoint LOSER's mentions to KEEPER and delete LOSER.

    Use after fixing a mis-resolved entity by hand. Both arguments
    are entity ids in the canonical ``"{type}:{slug}"`` form. Refuses
    to merge entities of different types.
    """
    repo = ctx.obj.entity_repository
    keeper_ent = repo.get_entity(keeper)
    loser_ent = repo.get_entity(loser)
    if keeper_ent is None:
        click.echo(f"❌ Keeper {keeper!r} not found", err=True)
        ctx.exit(1)
    if loser_ent is None:
        click.echo(f"❌ Loser {loser!r} not found", err=True)
        ctx.exit(1)
    if keeper_ent.type is not loser_ent.type:
        click.echo(
            f"❌ Cannot merge across types: {keeper_ent.type.value} ≠ {loser_ent.type.value}",
            err=True,
        )
        ctx.exit(1)

    label = "would merge" if dry_run else "merged"
    if dry_run:
        click.echo(f"  [dry-run] would repoint mentions {loser_ent.id!r} → {keeper_ent.id!r}")
        click.echo(f"  [dry-run] would delete {loser_ent.id!r}")
        click.echo(f"\n🎉 1 {label} (dry-run)")
        return

    moved = repo.repoint_mentions(from_entity_id=loser_ent.id, to_entity_id=keeper_ent.id)
    repo.delete_entity(loser_ent.id)
    click.echo(f"✓ {moved} mention(s) repointed; {loser_ent.id} deleted")
    click.echo(f"\n🎉 1 {label}")


# ---------------------------------------------------------------------------
# Spec #28 §1.13 — host / guest / recurring anchor metadata + override layer
# ---------------------------------------------------------------------------


@main.group("podcast")
def podcast_group():
    """Per-podcast metadata for the entity layer (hosts, recurring voices)."""


@podcast_group.command("set-hosts")
@click.argument("podcast_id")
@click.argument("entity_ids", nargs=-1, required=True)
@click.pass_context
@require_config
@log_command
def podcast_set_hosts(ctx, podcast_id, entity_ids):
    """Pin the canonical host(s) of a podcast.

    The extractor's anchor pass uses this list to short-circuit
    resolution for host name mentions in the body text.
    """
    repo = ctx.obj.entity_repository
    missing = [eid for eid in entity_ids if repo.get_entity(eid) is None]
    if missing:
        click.echo(f"❌ Unknown entity ids: {', '.join(missing)}", err=True)
        ctx.exit(1)
    repo.set_podcast_hosts(podcast_id, list(entity_ids))
    click.echo(f"✓ {podcast_id}: hosts = {', '.join(entity_ids)}")


@podcast_group.command("set-recurring")
@click.argument("podcast_id")
@click.argument("entity_ids", nargs=-1, required=True)
@click.pass_context
@require_config
@log_command
def podcast_set_recurring(ctx, podcast_id, entity_ids):
    """Pin recurring voices (regular co-hosts who aren't the lead host)."""
    repo = ctx.obj.entity_repository
    missing = [eid for eid in entity_ids if repo.get_entity(eid) is None]
    if missing:
        click.echo(f"❌ Unknown entity ids: {', '.join(missing)}", err=True)
        ctx.exit(1)
    repo.set_podcast_recurring(podcast_id, list(entity_ids))
    click.echo(f"✓ {podcast_id}: recurring = {', '.join(entity_ids)}")


@podcast_group.command("detect-hosts")
@click.argument("podcast_id")
@click.option("--limit", default=5, show_default=True, help="Top-N speaker labels to propose.")
@click.pass_context
@require_config
@log_command
def podcast_detect_hosts(ctx, podcast_id, limit):
    """Propose hosts by speaker frequency across all episodes of a podcast.

    Read-only — prints a ranked table the operator inspects, then
    follows up with ``set-hosts``. The function only sees mentions
    that already have a ``speaker`` value, so it works best after at
    least one extraction pass has run on this podcast.
    """
    repo = ctx.obj.entity_repository
    rows = repo.detect_top_speakers(podcast_id, limit=limit)
    if not rows:
        click.echo(
            f"No speaker data for {podcast_id} yet — run "
            "``thestill extract-entities --podcast-id {podcast_id}`` first."
        )
        return
    click.echo(f"Top {len(rows)} speakers for {podcast_id}:")
    click.echo(f"  {'rank':>4}  {'segments':>9}  speaker")
    for rank, (speaker, count) in enumerate(rows, 1):
        click.echo(f"  {rank:>4}  {count:>9}  {speaker}")
    click.echo("\nFollow up with: thestill podcast set-hosts <podcast_id> <entity_id> ...")


@main.group("episode")
def episode_group():
    """Per-episode metadata for the entity layer (guests)."""


@episode_group.command("set-guests")
@click.argument("episode_id")
@click.argument("entity_ids", nargs=-1, required=True)
@click.pass_context
@require_config
@log_command
def episode_set_guests(ctx, episode_id, entity_ids):
    """Pin the guest(s) of an episode.

    The extractor's anchor pass adds these to the host/recurring set
    for this one episode only — guests rotate, hosts don't.
    """
    repo = ctx.obj.entity_repository
    missing = [eid for eid in entity_ids if repo.get_entity(eid) is None]
    if missing:
        click.echo(f"❌ Unknown entity ids: {', '.join(missing)}", err=True)
        ctx.exit(1)
    repo.set_episode_guests(episode_id, list(entity_ids))
    click.echo(f"✓ {episode_id}: guests = {', '.join(entity_ids)}")


@main.command("backfill-roles")
@click.option(
    "--podcast-slug",
    default=None,
    help="Restrict backfill to one podcast (and its episodes). Omit to process the whole corpus.",
)
@click.pass_context
@require_config
@log_command
def backfill_roles(ctx, podcast_slug):
    """Link host/guest/recurring entities from existing facts files.

    Reads the LLM-generated ``data/podcast_facts/*.facts.md`` and
    ``data/episode_facts/**/*.facts.md`` files, resolves each named
    person against the entity index (creating a ``person:`` entity if
    none exists), and writes the ids into
    ``podcasts.host_entity_ids`` / ``podcasts.recurring_entity_ids`` /
    ``episodes.guest_entity_ids``.

    No LLM calls — this is a pure parse-and-link pass over data the
    summarize step already produced. Idempotent.
    """
    from .services.role_linker import backfill_all_roles, link_episode_roles, link_podcast_roles

    state = ctx.obj
    if podcast_slug:
        podcast = state.repository.get_by_slug(podcast_slug)
        if podcast is None:
            click.echo(f"❌ No podcast with slug={podcast_slug}", err=True)
            ctx.exit(1)
        result = link_podcast_roles(
            podcast_id=podcast.id,
            podcast_slug=podcast.slug,
            entity_repo=state.entity_repository,
            path_manager=state.path_manager,
        )
        click.echo(f"✓ {podcast.slug}: hosts={len(result.hosts)} recurring={len(result.recurring)}")
        eps_with_guests = 0
        eps_total = 0
        new_entities = len(set(result.created_entities))
        for episode in podcast.episodes:
            if not episode.slug:
                continue
            eps_total += 1
            ep_result = link_episode_roles(
                episode_id=episode.id,
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                entity_repo=state.entity_repository,
                path_manager=state.path_manager,
            )
            if ep_result.guests:
                eps_with_guests += 1
            new_entities += len(set(ep_result.created_entities))
        click.echo(f"✓ episodes: {eps_with_guests}/{eps_total} now have guests")
        click.echo(f"✓ new entities created: {new_entities}")
        return

    summary = backfill_all_roles(
        podcast_repo=state.repository,
        entity_repo=state.entity_repository,
        path_manager=state.path_manager,
    )
    click.echo(f"✓ podcasts: {summary.podcasts_with_hosts}/{summary.podcasts_processed} now have hosts")
    click.echo(f"✓ episodes: {summary.episodes_with_guests}/{summary.episodes_processed} now have guests")
    click.echo(f"✓ new entities created: {summary.entities_created}")
    if summary.skipped_names:
        unique = sorted(set(summary.skipped_names))
        click.echo(f"  ({len(unique)} unique names skipped — first-name-only or generic role labels)")


@main.command("backfill-inbox")
@click.option("--dry-run", is_flag=True, help="Show what would be delivered without writing rows.")
@click.pass_context
@require_config
@log_command
def backfill_inbox(ctx, dry_run):
    """Seed existing followers' inboxes with recent published episodes.

    Run once per database after the inbox migration lands. Idempotent —
    re-running is safe; already-delivered rows are skipped via the
    ``(user_id, episode_id)`` unique constraint.
    """
    count = ctx.obj.inbox_service.backfill_existing_followers(dry_run=dry_run)
    if dry_run:
        click.echo(f"✓ Dry run: {count} inbox rows would be delivered.")
    else:
        click.echo(f"✓ Backfill complete: {count} inbox rows delivered.")


# ---------------------------------------------------------------------------
# Spec #28 §1.13.7 — mention overrides + resolution blacklist
# ---------------------------------------------------------------------------


@main.group("mention")
def mention_group():
    """Per-mention overrides — drop bad rows, fix wrong resolutions."""


@mention_group.command("drop")
@click.argument("mention_id", type=int)
@click.option("--reason", default=None, help="Free-text justification (logged + stored).")
@click.option(
    "--scope-episode", is_flag=True, help="Limit the override to this mention's episode (default: global by surface)."
)
@click.pass_context
@require_config
@log_command
def mention_drop(ctx, mention_id, reason, scope_episode):
    """Soft-delete a mention. Stores a ``mention_overrides`` row so the
    drop survives a future reindex.
    """
    repo = ctx.obj.entity_repository
    mention = repo.get_mention(mention_id)
    if mention is None:
        click.echo(f"❌ No mention with id={mention_id}", err=True)
        ctx.exit(1)
    repo.add_override(
        surface_form=mention.surface_form,
        episode_id=mention.episode_id if scope_episode else None,
        kind="drop",
        reason=reason,
    )
    repo.resolve_mention(
        mention_id=mention_id,
        entity_id=None,
        status="dropped",
        method="override",
    )
    click.echo(f"✓ Dropped mention id={mention_id} (surface={mention.surface_form!r})")


@mention_group.command("repoint")
@click.argument("mention_id", type=int)
@click.argument("entity_id")
@click.option("--reason", default=None, help="Free-text justification.")
@click.option(
    "--scope-global", is_flag=True, help="Apply this override to every future mention with this surface form."
)
@click.pass_context
@require_config
@log_command
def mention_repoint(ctx, mention_id, entity_id, reason, scope_global):
    """Override a mention's resolution to point at a specific entity."""
    repo = ctx.obj.entity_repository
    mention = repo.get_mention(mention_id)
    if mention is None:
        click.echo(f"❌ No mention with id={mention_id}", err=True)
        ctx.exit(1)
    target = repo.get_entity(entity_id)
    if target is None:
        click.echo(f"❌ No entity with id={entity_id}", err=True)
        ctx.exit(1)
    repo.add_override(
        surface_form=mention.surface_form,
        episode_id=None if scope_global else mention.episode_id,
        kind="force_entity",
        entity_id=entity_id,
        reason=reason,
    )
    repo.resolve_mention(
        mention_id=mention_id,
        entity_id=entity_id,
        status="resolved",
        method="override",
    )
    click.echo(
        f"✓ Repointed mention id={mention_id} ({mention.surface_form!r}) " f"→ {entity_id} ({target.canonical_name})"
    )


@main.command("resolution-blacklist")
@click.argument("subcommand", type=click.Choice(["add", "list"]))
@click.argument("surface_form", required=False)
@click.argument("wrong_qid", required=False)
@click.option("--reason", default=None)
@click.pass_context
@require_config
@log_command
def resolution_blacklist(ctx, subcommand, surface_form, wrong_qid, reason):
    """Negative cache: refuse a (surface_form → wrong_qid) match.

    Consulted by the resolver before accepting any QID. Use after
    spotting a false positive like "Vercel" → "Vercel-Villedieu-le-Camp".
    """
    repo = ctx.obj.entity_repository
    if subcommand == "list":
        rows = repo.list_blacklist()
        if not rows:
            click.echo("(no blacklist entries)")
            return
        for r in rows:
            click.echo(f"  {r['id']:>4}  {r['surface_form']!r:<30}  {r['wrong_qid']:<10}  {r['reason'] or ''}")
        return
    # add
    if not surface_form or not wrong_qid:
        click.echo("Usage: resolution-blacklist add <surface_form> <wrong_qid> [--reason ...]", err=True)
        ctx.exit(1)
    repo.add_blacklist_entry(surface_form=surface_form, wrong_qid=wrong_qid, reason=reason)
    click.echo(f"✓ Blacklisted: {surface_form!r} ↛ {wrong_qid}")


# Augment the existing ``entity`` group with an ``alias-add`` command.
# (Defined here rather than at the entity_group def site to keep the
# Phase 1.13 additions co-located with the other 1.13 plumbing.)


@main.command("entity-alias-add")
@click.argument("entity_id")
@click.argument("alias")
@click.pass_context
@require_config
@log_command
def entity_alias_add(ctx, entity_id, alias):
    """Teach the resolver: ``<alias>`` is a synonym for ``<entity_id>``.

    Aliases are merged into ``entities.aliases`` and consulted by
    ``find_entity_by_name`` (powering ``find-mentions``, ``quotes-by``,
    etc.) and by the anchor expander (so e.g. "Karpathy" maps to
    person:andrej-karpathy when he's on the show).
    """
    repo = ctx.obj.entity_repository
    entity = repo.get_entity(entity_id)
    if entity is None:
        click.echo(f"❌ No entity with id={entity_id}", err=True)
        ctx.exit(1)
    if alias in entity.aliases:
        click.echo(f"(alias already present)")
        return
    entity.aliases = sorted(set(entity.aliases) | {alias})
    repo.upsert_entity(entity)
    click.echo(f"✓ {entity_id}: aliases += {alias!r}")


# ---------------------------------------------------------------------------
# Spec #28 §2.10 — chunks (sqlite-vec) backfill + entity-page rebuild
# ---------------------------------------------------------------------------


@main.group("chunks")
def chunks_group():
    """Manage the sqlite-vec chunk index that backs corpus search."""


@chunks_group.command("backfill")
@click.option("--podcast-id", default=None, help="Restrict to one podcast.")
@click.option("--max-episodes", "-m", default=None, type=int, help="Cap episodes processed.")
@click.option("--force", is_flag=True, help="Re-embed episodes already chunked at the current model.")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be processed; don't write.")
@click.pass_context
@require_config
@log_command
def chunks_backfill(ctx, podcast_id, max_episodes, force, dry_run):
    """Embed and index every episode that has a cleaned-transcript JSON sidecar."""
    from .core.chunk_writer import ChunkWriter
    from .models.annotated_transcript import AnnotatedTranscript

    podcast_repo = ctx.obj.repository
    path_manager = ctx.obj.path_manager
    config = ctx.obj.config

    if podcast_id:
        podcast = podcast_repo.get(podcast_id)
        if podcast is None:
            click.echo(f"❌ No podcast with id={podcast_id}", err=True)
            ctx.exit(1)
        candidate_eps = [(podcast, ep) for ep in (podcast.episodes or [])]
    else:
        candidate_eps = []
        for p in podcast_repo.get_all():
            for ep in p.episodes or []:
                candidate_eps.append((p, ep))

    eligible = [(p, e) for p, e in candidate_eps if e.clean_transcript_json_path]
    if max_episodes:
        eligible = eligible[:max_episodes]

    click.echo(f"Eligible episodes: {len(eligible)} (of {len(candidate_eps)} total)")
    if dry_run:
        for p, e in eligible[:20]:
            click.echo(f"  - {p.title} :: {e.title}")
        if len(eligible) > 20:
            click.echo(f"  … (+{len(eligible) - 20} more)")
        return

    writer = ChunkWriter(db_path=str(config.database_path), embedding_model=ctx.obj.embedding_model)

    inserted_total = 0
    skipped_total = 0
    for p, e in eligible:
        sidecar = path_manager.clean_transcript_file(e.clean_transcript_json_path)
        if not sidecar.exists():
            click.echo(f"  ! sidecar missing for {e.id} ({sidecar})")
            continue
        transcript = AnnotatedTranscript.model_validate_json(sidecar.read_text(encoding="utf-8"))
        inserted = writer.write_episode(e.id, transcript, force=force)
        if inserted:
            inserted_total += inserted
            click.echo(f"  ✓ {p.title} :: {e.title} ({inserted} chunks)")
        else:
            skipped_total += 1

    click.echo(f"✓ chunks backfill complete: {inserted_total} inserted, {skipped_total} skipped")


@main.command("rebuild-entity-pages")
@click.option(
    "--type",
    "entity_type",
    type=click.Choice(["person", "company", "topic"]),
    default=None,
    help="Restrict to one entity type.",
)
@click.pass_context
@require_config
@log_command
def rebuild_entity_pages(ctx, entity_type):
    """Regenerate Obsidian entity Markdown pages from the entity DB."""
    from .core.entity_page_writer import EntityPageWriter
    from .models.entities import EntityType

    writer = EntityPageWriter(
        path_manager=ctx.obj.path_manager,
        entity_repository=ctx.obj.entity_repository,
    )
    et = EntityType(entity_type) if entity_type else None
    result = writer.write_all(entity_type=et)
    click.echo(f"✓ {len(result.written)} entity pages written, " f"{len(result.skipped_unchanged)} unchanged")


# ---------------------------------------------------------------------------
# Spec #28 §1.12 — harness eval (offline analogue of the Claude Desktop gate)
# ---------------------------------------------------------------------------


@main.command("harness-eval")
@click.option(
    "--questions-file",
    default="tests/fixtures/eval/harness_reference_questions.json",
    help="Path to the harness JSON file.",
)
@click.option("--limit-per-question", default=20, type=int, help="Max rows per tool call.")
@click.option("--json", "json_output", is_flag=True, help="Emit a JSON report instead of TTY.")
@click.pass_context
@require_config
@log_command
def harness_eval(ctx, questions_file, limit_per_question, json_output):
    """Run the 10 harness reference questions against the entity tools.

    Spec #28 §1.12 acceptance gate. The full gate has Claude Desktop
    in the loop and verifies no-fabrication; this offline grader is
    the floor — verifies the tool surface + the resolved data
    actually supports each question.

    Per-question status:
    - PASS — every expected entity resolves, the requested fragments
      appear in at least one returned quote, and ``min_distinct_episodes``
      is met.
    - FAIL — entities resolved but quotes/episode threshold not met
      (data is too sparse or the resolver mis-resolved).
    - SKIP — one or more expected entities don't exist yet (corpus
      hasn't been processed for that question's data).
    """
    from .repositories.sqlite_entity_repository import SqliteEntityRepository

    payload_path = Path(questions_file)
    if not payload_path.is_absolute():
        payload_path = Path.cwd() / payload_path
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    questions = payload["questions"]
    repo: SqliteEntityRepository = ctx.obj.entity_repository

    results = [_grade_one(repo, q, limit_per_question) for q in questions]

    summary = {
        "pass": sum(1 for r in results if r["status"] == "pass"),
        "fail": sum(1 for r in results if r["status"] == "fail"),
        "skip": sum(1 for r in results if r["status"] == "skip"),
        "total": len(results),
    }

    if json_output:
        click.echo(json.dumps({"results": results, "summary": summary}, indent=2, default=str))
        return

    for r in results:
        symbol = {"pass": "✓", "fail": "✗", "skip": "·"}[r["status"]]
        click.echo(f"{symbol} {r['id']}  {r['question'][:90]}")
        for note in r["notes"]:
            click.echo(f"    {note}")

    click.echo()
    click.echo(f"🎉 {summary['pass']}/{summary['total']} pass, " f"{summary['fail']} fail, {summary['skip']} skip")

    # Exit non-zero only when something genuinely failed. Skips are
    # expected when the corpus hasn't been fully resolved yet — they
    # signal "data not ready", not "code broken".
    if summary["fail"] > 0:
        ctx.exit(2)


def _grade_one(repo, question: dict, limit: int) -> dict:
    """Walk a harness question through the entity tools and grade it."""
    notes = []
    expected_entities = question.get("expected_entities", [])
    expected_fragments = [f.lower() for f in question.get("expected_quote_fragments", [])]
    min_eps = int(question.get("min_distinct_episodes", 1))

    resolved_entities = []
    for name in expected_entities:
        ent = repo.find_entity_by_name(name)
        if ent is None:
            notes.append(f"unresolved entity: {name!r}")
            continue
        resolved_entities.append(ent)

    if not resolved_entities and expected_entities:
        return {
            "id": question["id"],
            "question": question["question"],
            "status": "skip",
            "notes": notes or ["no expected entities resolved"],
        }

    rows = []
    for ent in resolved_entities:
        rows.extend(repo.find_mentions(entity_id=ent.id, limit=limit))

    distinct_episodes = {r.episode_id for r in rows}
    fragment_hits = []
    if expected_fragments:
        for frag in expected_fragments:
            hit_episodes = {r.episode_id for r in rows if frag in (r.mention.quote_excerpt or "").lower()}
            fragment_hits.append((frag, len(hit_episodes)))
        missing = [f for f, n in fragment_hits if n == 0]
        if missing:
            notes.append(f"fragments not found in any returned quote: {missing}")

    notes.append(
        f"resolved {len(resolved_entities)}/{len(expected_entities)} entities, "
        f"{len(rows)} mentions across {len(distinct_episodes)} episode(s)"
    )

    if not rows:
        return {
            "id": question["id"],
            "question": question["question"],
            "status": "skip",
            "notes": notes + ["entity exists but no resolved mentions yet"],
        }

    status = "pass"
    if len(distinct_episodes) < min_eps:
        notes.append(f"min_distinct_episodes not met: got {len(distinct_episodes)}, need {min_eps}")
        status = "fail"
    if expected_fragments and any(n == 0 for _, n in fragment_hits):
        status = "fail"

    return {
        "id": question["id"],
        "question": question["question"],
        "status": status,
        "notes": notes,
    }


@main.command()
@click.option("--host", "-h", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
@click.option("--port", "-p", default=8000, type=int, help="Port to bind to (default: 8000)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--workers", "-w", default=1, type=int, help="Number of worker processes (default: 1)")
@click.pass_context
@require_config
@log_command
def server(ctx, host, port, reload, workers):
    """Start the web server for webhooks and API.

    The web server provides:
    - Webhook endpoints for receiving transcription callbacks (ElevenLabs)
    - Health check and status endpoints
    - REST API for podcast management (future)
    - Web UI for browsing content (future)

    Examples:
        thestill server                      # Start on localhost:8000
        thestill server --port 8080          # Custom port
        thestill server --host 0.0.0.0       # Bind to all interfaces
        thestill server --reload             # Auto-reload for development
    """
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError as e:
        click.echo(f"❌ Web server dependencies not installed: {e}", err=True)
        click.echo("   Install with: pip install 'thestill[web]'", err=True)
        ctx.exit(1)

    config = ctx.obj.config

    click.echo("🌐 Starting thestill web server...")
    click.echo(f"   Host: {host}")
    click.echo(f"   Port: {port}")
    click.echo(f"   Storage: {config.storage_path}")
    click.echo(f"   Database: {config.database_path}")

    if reload:
        click.echo("   Mode: Development (auto-reload enabled)")
    else:
        click.echo(f"   Workers: {workers}")

    click.echo("")
    click.echo(f"📡 Webhook URL: http://{host}:{port}/webhook/elevenlabs/speech-to-text")
    click.echo(f"📊 Status URL: http://{host}:{port}/status")
    click.echo(f"📚 API Docs: http://{host}:{port}/docs")
    click.echo("")

    # Create app with existing config to share services
    app = create_app(config)

    # Run uvicorn
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,  # Can't use workers with reload
        log_level="info",
    )


if __name__ == "__main__":
    main()
