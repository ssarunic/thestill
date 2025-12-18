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

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import click

# Import thestill modules using relative imports
# This module can be executed in two ways:
# 1. Package mode (recommended): `thestill` command (defined in pyproject.toml entry point)
# 2. Module mode (development): `python -m thestill.cli` (uses __main__ guard at bottom)
from .core.audio_downloader import AudioDownloader
from .core.audio_preprocessor import AudioPreprocessor
from .core.elevenlabs_transcriber import ElevenLabsTranscriber
from .core.evaluator import PostProcessorEvaluator, TranscriptEvaluator, print_evaluation_summary
from .core.external_transcript_downloader import ExternalTranscriptDownloader
from .core.feed_manager import PodcastFeedManager
from .core.google_transcriber import GoogleCloudTranscriber
from .core.llm_provider import create_llm_provider
from .core.post_processor import TranscriptSummarizer
from .core.whisper_transcriber import WhisperTranscriber, WhisperXTranscriber
from .repositories.sqlite_podcast_repository import SqlitePodcastRepository
from .services import PodcastService, RefreshService, StatsService
from .utils.cli_formatter import CLIFormatter
from .utils.config import load_config
from .utils.duration import format_duration, format_speed_stats, get_audio_duration, parse_duration
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


@click.group()
@click.option("--config", "-c", help="Path to config file")
@click.pass_context
def main(ctx, config):
    """thestill.me - Automated podcast transcription and summarization"""
    # Initialize logging to stderr (important for MCP server compatibility)
    setup_logger("thestill", log_level="INFO", console_output=True)

    try:
        config_obj = load_config(config)
        click.echo("✓ Configuration loaded successfully")

        # Initialize all shared services once (dependency injection)
        storage_path = config_obj.storage_path  # Path object
        path_manager = PathManager(str(storage_path))
        repository = SqlitePodcastRepository(db_path=config_obj.database_path)
        podcast_service = PodcastService(storage_path, repository, path_manager)
        stats_service = StatsService(storage_path, repository, path_manager)
        feed_manager = PodcastFeedManager(repository, path_manager)
        audio_downloader = AudioDownloader(str(path_manager.original_audio_dir()))
        audio_preprocessor = AudioPreprocessor()
        external_transcript_downloader = ExternalTranscriptDownloader(repository, path_manager)

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
        )

    except Exception as e:
        click.echo(f"❌ Configuration error: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument("rss_url")
@click.pass_context
def add(ctx, rss_url):
    """Add a podcast RSS feed"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    podcast = ctx.obj.podcast_service.add_podcast(rss_url)
    if podcast:
        click.echo(f"✓ Podcast added: {podcast.title}")
    else:
        click.echo("❌ Failed to add podcast or podcast already exists", err=True)


@main.command()
@click.argument("podcast_id")
@click.pass_context
def remove(ctx, podcast_id):
    """Remove a podcast by RSS URL or index number"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    if ctx.obj.podcast_service.remove_podcast(podcast_id):
        click.echo("✓ Podcast removed")
    else:
        click.echo("❌ Podcast not found", err=True)


@main.command()
@click.pass_context
def list(ctx):
    """List all tracked podcasts"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    podcasts = ctx.obj.podcast_service.list_podcasts()
    output = CLIFormatter.format_podcast_list(podcasts)
    click.echo(output)


@main.command()
@click.option("--podcast-id", help="Refresh specific podcast (index or RSS URL)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to discover per podcast")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be discovered without updating feeds.json")
@click.pass_context
def refresh(ctx, podcast_id, max_episodes, dry_run):
    """Refresh podcast feeds and discover new episodes (step 1)"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
def download(ctx, podcast_id, max_episodes, dry_run):
    """Download audio files for episodes that need downloading (step 2)"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
                    duration_str = str(duration_seconds) if duration_seconds else None

                    # Store the relative path (includes podcast subdirectory)
                    feed_manager.mark_episode_downloaded(
                        str(podcast.rss_url), episode.external_id, audio_path, duration=duration_str
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
def downsample(ctx, podcast_id, max_episodes, dry_run):
    """Downsample downloaded audio to 16kHz, 16-bit, mono WAV format"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
                    duration_str = str(duration_seconds) if duration_seconds else None

                    feed_manager.mark_episode_downsampled(
                        str(podcast.rss_url), episode.external_id, relative_path, duration=duration_str
                    )
                    downsampled_count += 1
                    click.echo("✅ Downsampled successfully")
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
def clean_transcript(ctx, dry_run, max_episodes, force, stream):
    """Clean transcripts using facts-based two-pass approach"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    import json

    from .core.llm_provider import create_llm_provider
    from .core.transcript_cleaning_processor import TranscriptCleaningProcessor

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager

    # Create LLM provider
    try:
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
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
    stream_callback = None
    if stream:

        def stream_callback(chunk: str) -> None:
            """Print LLM output chunks in real-time."""
            sys.stdout.write(chunk)
            sys.stdout.flush()

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
            )

            # Add newline after streaming completes
            if stream:
                click.echo("")  # End the streamed output with newline

            if result:
                # Update feed manager
                # Note: raw_transcript_path is preserved (episode.raw_transcript_path)
                # Only clean_transcript_path is updated
                feed_manager.mark_episode_processed(
                    str(podcast.rss_url),
                    episode.external_id,
                    raw_transcript_path=episode.raw_transcript_path,
                    clean_transcript_path=clean_transcript_db_path,
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
def facts_list(ctx):
    """List all facts files"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
def facts_show(ctx, podcast_id, episode_id):
    """Show facts for a podcast or episode"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
def facts_edit(ctx, podcast_id, episode_id):
    """Open facts file in $EDITOR"""
    import os
    import subprocess

    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
def facts_extract(ctx, podcast_id, episode_id, force):
    """Extract facts from a transcript"""
    import json

    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
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
        )
        facts_manager.save_podcast_facts(podcast_slug, podcast_facts)
        click.echo(f"✓ Saved podcast facts: {facts_manager.get_podcast_facts_path(podcast_slug)}")

    click.echo("\n✅ Facts extraction complete!")
    click.echo(f"   Speakers identified: {len(episode_facts.speaker_mapping)}")
    click.echo(f"   Guests: {len(episode_facts.guests)}")
    click.echo(f"   Topics: {len(episode_facts.topics_keywords)}")


@main.command()
@click.pass_context
def status(ctx):
    """Show system status and statistics"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    # Use shared services from context
    config = ctx.obj.config
    stats_service = ctx.obj.stats_service

    click.echo(CLIFormatter.format_header("thestill.me Status"))

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
@click.option("--dry-run", is_flag=True, help="Preview what would be deleted without actually deleting")
@click.pass_context
def cleanup(ctx, dry_run):
    """Clean up old audio files"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config
    downloader = ctx.obj.audio_downloader

    if dry_run:
        click.echo(f"🧹 [DRY RUN] Previewing cleanup of files older than {config.cleanup_days} days...")
    else:
        click.echo(f"🧹 Cleaning up files older than {config.cleanup_days} days...")

    count = downloader.cleanup_old_files(config.cleanup_days, dry_run=dry_run)

    if dry_run:
        if count > 0:
            click.echo(f"✓ Would delete {count} file(s) (dry-run mode)")
        else:
            click.echo("✓ No files would be deleted")
    else:
        if count > 0:
            click.echo(f"✓ Cleanup complete - deleted {count} file(s)")
        else:
            click.echo("✓ Cleanup complete - no files to delete")


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
@click.pass_context
def transcribe(ctx, audio_path, downsample, podcast_id, episode_id, max_episodes, dry_run, cancel_pending):
    """Transcribe audio files to JSON transcripts.

    Without arguments: Transcribes all downloaded episodes that need transcription.
    With audio_path: Transcribes a specific audio file (standalone mode).
    """
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config
    preprocessor = ctx.obj.audio_preprocessor

    # Initialize the appropriate transcriber based on config settings
    transcriber = None
    if config.transcription_provider.lower() == "google":
        click.echo("🎤 Using Google Cloud Speech-to-Text")
        if not config.google_app_credentials and not config.google_cloud_project_id:
            click.echo("❌ Google Cloud credentials not configured", err=True)
            click.echo("   Set GOOGLE_APP_CREDENTIALS and GOOGLE_CLOUD_PROJECT_ID in .env", err=True)
            ctx.exit(1)

        try:
            transcriber = GoogleCloudTranscriber(
                credentials_path=config.google_app_credentials or None,
                project_id=config.google_cloud_project_id or None,
                storage_bucket=config.google_storage_bucket or None,
                enable_diarization=config.enable_diarization,
                min_speakers=config.min_speakers,
                max_speakers=config.max_speakers,
                parallel_chunks=config.max_workers,
                path_manager=config.path_manager,
            )

            # Check for pending operations from previous runs
            # Note: For chunked transcriptions, individual chunks are persisted as operations.
            # These are handled by _transcribe_chunked when the episode is processed again.
            # We only need to handle --cancel-pending here to clean up operations.
            pending_ops = transcriber.list_pending_operations()
            if pending_ops:
                # Group by episode to show user-friendly count
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
                    # Cancel mode: download completed, cancel still-running
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
                    # Normal mode: pending operations will be resumed when their episodes are processed
                    click.echo("   These will be resumed when the episode is transcribed again.")
                    click.echo("   (Use --cancel-pending to discard and start fresh)")
                    click.echo("")

        except ImportError as e:
            click.echo(f"❌ {e}", err=True)
            click.echo("   Install with: pip install google-cloud-speech google-cloud-storage", err=True)
            ctx.exit(1)
    elif config.transcription_provider.lower() == "elevenlabs":
        click.echo("🎤 Using ElevenLabs Speech-to-Text")
        if not config.elevenlabs_api_key:
            click.echo("❌ ElevenLabs API key not configured", err=True)
            click.echo("   Set ELEVENLABS_API_KEY in .env", err=True)
            ctx.exit(1)

        # Start background webhook server for async transcription callbacks
        # Do this BEFORE creating transcriber so we know if webhook mode is available
        from .web import BackgroundWebhookServer, ExistingServerInfo, webhook_server_context

        webhook_server = webhook_server_context(
            config=config,
            port=config.webhook_server_port,
        )
        # Store in ctx.obj for cleanup at end of command
        ctx.obj.webhook_server_context = webhook_server
        server = webhook_server.__enter__()
        ctx.obj.started_webhook_server = isinstance(server, BackgroundWebhookServer)

        # Determine if we have a working webhook server
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

        transcriber = ElevenLabsTranscriber(
            api_key=config.elevenlabs_api_key,
            model=config.elevenlabs_model,
            enable_diarization=config.enable_diarization,
            num_speakers=config.max_speakers,  # ElevenLabs uses num_speakers instead of min/max
            path_manager=config.path_manager,
            use_async=True,  # Enable async mode for webhook callbacks
            async_threshold_mb=config.elevenlabs_async_threshold_mb,  # 0 = always async
            tag_audio_events=True,  # Tag audio events like laughter, applause, etc.
            wait_for_webhook=webhook_available,  # Don't poll if webhook server is available
        )
        # Store webhook mode flag for handling transcription results
        ctx.obj.using_webhook_mode = webhook_available
    elif config.transcription_model.lower() == "parakeet":
        from .core.parakeet_transcriber import ParakeetTranscriber

        transcriber = ParakeetTranscriber(config.whisper_device)
    elif config.enable_diarization:
        click.echo("🎤 Using WhisperX with speaker diarization enabled")
        transcriber = WhisperXTranscriber(
            model_name=config.whisper_model,
            device=config.whisper_device,
            enable_diarization=True,
            hf_token=config.huggingface_token,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            diarization_model=config.diarization_model,
        )
    else:
        click.echo(f"🎤 Using Whisper model: {config.whisper_model}")
        transcriber = WhisperTranscriber(config.whisper_model, config.whisper_device)

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

            transcript_data = transcriber.transcribe_audio(
                transcription_audio_path,
                output,
                clean_transcript=config.enable_transcript_cleaning,
                cleaning_config=cleaning_config,
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
            click.echo(
                f"  • {episode.title} ({episode.pub_date.strftime('%Y-%m-%d') if episode.pub_date else 'no date'})"
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

            click.echo(f"\n🎧 {episode.title}")

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

                # Pass episode context for Google Cloud operation persistence
                # This allows resuming transcriptions if the app is restarted
                transcript_data = transcriber.transcribe_audio(
                    transcription_audio_path,
                    output,
                    clean_transcript=config.enable_transcript_cleaning,
                    cleaning_config=cleaning_config,
                    episode_id=episode.id,
                    podcast_slug=podcast.slug,
                    episode_slug=episode.slug,
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
                    episode_duration = parse_duration(episode.duration)
                    if episode_duration:
                        total_audio_seconds += episode_duration

                    click.echo("✅ Transcription complete!")
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
def summarize(ctx, transcript_path, output, dry_run, max_episodes, force):
    """Summarize cleaned transcripts with comprehensive analysis.

    If TRANSCRIPT_PATH is provided, summarizes that specific file.
    Otherwise, finds the next cleaned transcript(s) without a summary.

    Produces executive summary, notable quotes, content angles, social snippets,
    resource check, and critical analysis.
    """
    if ctx.obj is None:
        click.echo("Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager

    # Create LLM provider
    try:
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
    except Exception as e:
        click.echo(f"Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    summarizer = TranscriptSummarizer(llm_provider)

    # If transcript_path provided, summarize that specific file
    if transcript_path:
        transcript_path_obj = Path(transcript_path).resolve()
        with open(transcript_path_obj, "r", encoding="utf-8") as f:
            transcript_text = f.read()

        if output:
            output_path = Path(output)
        else:
            # Try to extract podcast slug from path (e.g., data/clean_transcripts/<slug>/file.md)
            # to preserve folder structure in summaries
            clean_transcripts_dir = path_manager.clean_transcripts_dir().resolve()
            try:
                relative_path = transcript_path_obj.relative_to(clean_transcripts_dir)
                # If file is in a podcast subfolder, preserve that structure
                if len(relative_path.parts) > 1:
                    podcast_slug = relative_path.parts[0]
                    summary_filename = f"{transcript_path_obj.stem}_summary.md"
                    output_path = path_manager.summaries_dir() / podcast_slug / summary_filename
                else:
                    output_path = path_manager.summary_file(f"{transcript_path_obj.stem}_summary.md")
            except ValueError:
                # Path is not under clean_transcripts_dir, use flat structure
                output_path = path_manager.summary_file(f"{transcript_path_obj.stem}_summary.md")

        click.echo(f"Summarizing transcript with {llm_provider.get_model_name()}...")
        try:
            summarizer.summarize(transcript_text, output_path)
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

            click.echo(f"Summarizing with {llm_provider.get_model_name()}...")
            summarizer.summarize(transcript_text, output_path)

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


@main.command("evaluate-raw-transcript")
@click.argument("transcript_path", type=click.Path(exists=True), required=False)
@click.option("--output", "-o", help="Output path for evaluation report (standalone mode only)")
@click.option("--podcast-id", help="Evaluate transcripts from specific podcast (index, URL, or UUID)")
@click.option("--episode-id", help="Evaluate specific episode (requires --podcast-id)")
@click.option("--max-episodes", "-m", type=int, help="Maximum episodes to evaluate")
@click.option("--dry-run", "-d", is_flag=True, help="Preview what would be evaluated")
@click.option("--force", "-f", is_flag=True, help="Re-evaluate even if evaluation exists")
@click.pass_context
def evaluate_raw_transcript(ctx, transcript_path, output, podcast_id, episode_id, max_episodes, dry_run, force):
    """Evaluate the quality of raw transcripts.

    If TRANSCRIPT_PATH is provided, evaluates that specific file.
    Otherwise, discovers episodes with raw transcripts and evaluates them in batch.

    Uses LLM to analyze transcript quality including accuracy, completeness,
    entity handling, and structural clarity.
    """
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager
    podcast_service = ctx.obj.podcast_service

    # Create LLM provider
    try:
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    evaluator = TranscriptEvaluator(llm_provider)

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
            print_evaluation_summary(evaluation, "transcript")
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
            print_evaluation_summary(evaluation, "transcript")
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
def evaluate_clean_transcript(
    ctx, transcript_path, original, output, podcast_id, episode_id, max_episodes, dry_run, force
):
    """Evaluate the quality of clean transcripts.

    If TRANSCRIPT_PATH is provided, evaluates that specific file.
    Otherwise, discovers episodes with clean transcripts and evaluates them in batch.

    Uses LLM to analyze fidelity, formatting, readability, and enhancements.
    """
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config
    path_manager = ctx.obj.path_manager
    feed_manager = ctx.obj.feed_manager
    podcast_service = ctx.obj.podcast_service

    # Create LLM provider
    try:
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    evaluator = PostProcessorEvaluator(llm_provider)

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
            print_evaluation_summary(evaluation, "clean-transcript")
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
            print_evaluation_summary(evaluation, "clean-transcript")
            click.echo(f"   ✓ Saved: {eval_path}")
            total_processed += 1

        except Exception as e:
            click.echo(f"   ❌ Error: {e}", err=True)
            import traceback

            traceback.print_exc()

    total_time = time.time() - start_time
    click.echo("\n🎉 Evaluation complete!")
    click.echo(f"✓ {total_processed} transcript(s) evaluated in {total_time:.1f} seconds")


@main.command()
@click.option("--host", "-h", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
@click.option("--port", "-p", default=8000, type=int, help="Port to bind to (default: 8000)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--workers", "-w", default=1, type=int, help="Number of worker processes (default: 1)")
@click.pass_context
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
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

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
