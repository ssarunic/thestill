# Copyright 2025 thestill.ai
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

import sys
import time
from pathlib import Path

import click

# Import thestill modules using relative imports
# This module can be executed in two ways:
# 1. Package mode (recommended): `thestill` command (defined in pyproject.toml entry point)
# 2. Module mode (development): `python -m thestill.cli` (uses __main__ guard at bottom)
from .core.audio_downloader import AudioDownloader
from .core.audio_preprocessor import AudioPreprocessor
from .core.evaluator import PostProcessorEvaluator, TranscriptEvaluator, print_evaluation_summary
from .core.feed_manager import PodcastFeedManager
from .core.google_transcriber import GoogleCloudTranscriber
from .core.llm_provider import create_llm_provider
from .core.post_processor import EnhancedPostProcessor, PostProcessorConfig
from .core.transcriber import WhisperTranscriber, WhisperXTranscriber
from .repositories.sqlite_podcast_repository import SqlitePodcastRepository
from .services import PodcastService, RefreshService, StatsService
from .utils.cli_formatter import CLIFormatter
from .utils.config import load_config
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
    ):
        self.config = config
        self.path_manager = path_manager
        self.repository = repository
        self.podcast_service = podcast_service
        self.stats_service = stats_service
        self.feed_manager = feed_manager
        self.audio_downloader = audio_downloader
        self.audio_preprocessor = audio_preprocessor


@click.group()
@click.option("--config", "-c", help="Path to config file")
@click.pass_context
def main(ctx, config):
    """thestill.ai - Automated podcast transcription and summarization"""
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
                audio_path = downloader.download_episode(episode, podcast.title)

                if audio_path:
                    # Store just the filename
                    audio_filename = Path(audio_path).name

                    feed_manager.mark_episode_downloaded(str(podcast.rss_url), episode.external_id, audio_filename)
                    downloaded_count += 1
                    click.echo("✅ Downloaded successfully")
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

                # Downsample
                click.echo("🔧 Downsampling to 16kHz, 16-bit, mono WAV...")
                downsampled_path = preprocessor.downsample_audio(
                    str(original_audio_file), str(config.path_manager.downsampled_audio_dir())
                )

                if downsampled_path:
                    # Store just the filename
                    downsampled_filename = Path(downsampled_path).name

                    feed_manager.mark_episode_downsampled(
                        str(podcast.rss_url), episode.external_id, downsampled_filename
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


@main.command()
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be processed without actually processing")
@click.option("--max-episodes", "-m", default=5, help="Maximum episodes to process per podcast")
@click.pass_context
def clean_transcript(ctx, dry_run, max_episodes):
    """Clean existing transcripts with LLM post-processing"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    import json
    from datetime import datetime

    from .core.transcript_cleaning_processor import TranscriptCleaningProcessor
    from .models.podcast import CleanedTranscript

    # Use shared services from context
    config = ctx.obj.config
    feed_manager = ctx.obj.feed_manager

    # Create LLM provider based on configuration
    try:
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
        click.echo(f"✓ Using {config.llm_provider.upper()} provider with model: {llm_provider.get_model_name()}")
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    cleaning_processor = TranscriptCleaningProcessor(llm_provider)

    # Find all transcripts that haven't been cleaned yet
    click.echo("🔍 Looking for transcripts to clean...")

    podcasts = feed_manager.list_podcasts()
    transcripts_to_clean = []

    for podcast in podcasts:
        for episode in podcast.episodes:
            # Safety: Check if transcript file actually exists (not just path is set)
            if episode.raw_transcript_path:
                transcript_path = config.path_manager.raw_transcript_file(episode.raw_transcript_path)
                if not transcript_path.exists():
                    continue  # Skip if transcript file doesn't exist

                # Check if clean transcript file exists (not just if path is set)
                clean_transcript_exists = False
                if episode.clean_transcript_path:
                    clean_transcript_path = config.path_manager.clean_transcript_file(episode.clean_transcript_path)
                    clean_transcript_exists = clean_transcript_path.exists()

                # Only process if raw transcript exists but clean transcript doesn't
                if not clean_transcript_exists:
                    transcripts_to_clean.append((podcast, episode, transcript_path))

    if not transcripts_to_clean:
        click.echo("✓ No transcripts found to clean")
        return

    total_transcripts = min(len(transcripts_to_clean), max_episodes) if max_episodes else len(transcripts_to_clean)
    click.echo(f"📄 Found {len(transcripts_to_clean)} transcripts. Processing {total_transcripts} episodes")

    if dry_run:
        for podcast, episode, _ in transcripts_to_clean[:max_episodes]:
            click.echo(f"  • {podcast.title}: {episode.title}")
        click.echo("\n(Run without --dry-run to actually process)")
        return

    total_processed = 0
    start_time = time.time()

    # Progress bar wrapper
    with click.progressbar(
        transcripts_to_clean[:max_episodes],
        label="Cleaning",
        show_pos=True,  # Show "X/Y" counter
        show_eta=True,  # Show estimated time
        file=sys.stderr,  # Use stderr (consistent with logging)
        item_show_func=lambda x: None,  # Disable default item display
    ) as bar:
        for podcast, episode, transcript_path in bar:
            click.echo(f"\n📻 {podcast.title}")
            click.echo(f"🎧 {episode.title}")
            click.echo("─" * 50)

            try:
                # Load transcript
                with open(transcript_path, "r", encoding="utf-8") as f:
                    transcript_data = json.load(f)

                # Clean transcript with context
                # Generate filename matching pattern: Podcast_Episode_hash_cleaned.md
                base_name = transcript_path.stem  # Remove .json extension
                if base_name.endswith("_transcript"):
                    base_name = base_name[: -len("_transcript")]  # Remove _transcript suffix
                cleaned_filename = f"{base_name}_cleaned.md"
                cleaned_path = config.path_manager.clean_transcripts_dir() / cleaned_filename

                result = cleaning_processor.clean_transcript(
                    transcript_data=transcript_data,
                    podcast_title=podcast.title,
                    podcast_description=podcast.description,
                    episode_title=episode.title,
                    episode_description=episode.description,
                    episode_external_id=episode.external_id,
                    episode_id=episode.id,  # Pass internal UUID for file naming
                    output_path=str(cleaned_path),
                    save_corrections=True,
                    save_metrics=True,
                )

                if result:
                    # Create CleanedTranscript model and save
                    _ = CleanedTranscript(
                        episode_external_id=episode.external_id,
                        episode_title=episode.title,
                        podcast_title=podcast.title,
                        corrections=result["corrections"],
                        speaker_mapping=result["speaker_mapping"],
                        cleaned_markdown=result["cleaned_markdown"],
                        processing_time=result["processing_time"],
                        created_at=datetime.now(),
                    )

                    # Update feed manager to mark as processed
                    # Generate clean transcript filename matching pattern:
                    # Raw: Podcast_Episode_hash_transcript.json → Clean: Podcast_Episode_hash_cleaned.md
                    base_name = transcript_path.stem  # Remove .json extension
                    if base_name.endswith("_transcript"):
                        base_name = base_name[: -len("_transcript")]  # Remove _transcript suffix
                    cleaned_md_filename = f"{base_name}_cleaned.md"

                    feed_manager.mark_episode_processed(
                        str(podcast.rss_url),
                        episode.external_id,
                        raw_transcript_path=transcript_path.name,  # Just the raw transcript filename
                        clean_transcript_path=cleaned_md_filename,  # Podcast_Episode_hash_cleaned.md
                    )

                    total_processed += 1
                    click.echo("✅ Transcript cleaned successfully!")
                    click.echo(f"🔧 Corrections applied: {len(result['corrections'])}")
                    click.echo(f"👥 Speakers identified: {len(result['speaker_mapping'])}")

            except Exception as e:
                click.echo(f"❌ Error cleaning transcript: {e}")
                import traceback

                traceback.print_exc()
                continue

    total_time = time.time() - start_time
    click.echo("\n🎉 Processing complete!")
    click.echo(f"✓ {total_processed} transcripts cleaned in {total_time:.1f} seconds")


# ============================================================================
# Facts management commands (for transcript cleaning v2)
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

    # Episode facts
    click.echo("\n🎧 Episode Facts:")
    if episode_facts_dir.exists():
        episode_files = list(episode_facts_dir.glob("*.facts.md"))
        if episode_files:
            click.echo(f"  {len(episode_files)} episode facts files")
            # Show first 10
            for f in sorted(episode_files)[:10]:
                click.echo(f"  • {f.name}")
            if len(episode_files) > 10:
                click.echo(f"  ... and {len(episode_files) - 10} more")
        else:
            click.echo("  (no episode facts files)")
    else:
        click.echo("  (directory not created)")


@facts.command("show")
@click.option("--podcast-id", "-p", help="Podcast ID (index or URL)")
@click.option("--episode-id", "-e", help="Episode UUID")
@click.pass_context
def facts_show(ctx, podcast_id, episode_id):
    """Show facts for a podcast or episode"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    from .core.facts_manager import FactsManager, slugify

    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository
    facts_manager = FactsManager(path_manager)

    if episode_id:
        # Show episode facts
        episode_facts = facts_manager.load_episode_facts(episode_id)
        if episode_facts:
            click.echo(CLIFormatter.format_header(f"Episode Facts: {episode_facts.episode_title}"))
            click.echo(facts_manager.get_episode_facts_markdown(episode_id))
        else:
            click.echo(f"❌ No facts file found for episode: {episode_id}")
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

        podcast_slug = slugify(podcast.title)
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
@click.option("--episode-id", "-e", help="Episode UUID")
@click.pass_context
def facts_edit(ctx, podcast_id, episode_id):
    """Open facts file in $EDITOR"""
    import os
    import subprocess

    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    from .core.facts_manager import FactsManager, slugify

    path_manager = ctx.obj.path_manager
    repository = ctx.obj.repository
    facts_manager = FactsManager(path_manager)

    editor = os.environ.get("EDITOR", "nano")
    file_path = None

    if episode_id:
        file_path = facts_manager.get_episode_facts_path(episode_id)
    elif podcast_id:
        podcast = None
        if podcast_id.isdigit():
            podcast = repository.get_by_index(int(podcast_id))
        elif podcast_id.startswith("http"):
            podcast = repository.get_by_url(podcast_id)

        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}")
            ctx.exit(1)

        podcast_slug = slugify(podcast.title)
        file_path = facts_manager.get_podcast_facts_path(podcast_slug)
    else:
        click.echo("❌ Please specify --podcast-id or --episode-id")
        ctx.exit(1)

    if not file_path.exists():
        click.echo(f"❌ Facts file not found: {file_path}")
        click.echo("   Run clean-transcript-v2 first to generate facts.")
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
    from .core.facts_manager import FactsManager, slugify
    from .core.llm_provider import create_llm_provider

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
    podcast_slug = slugify(podcast.title)
    if not force:
        if facts_manager.load_episode_facts(episode.id):
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
    facts_manager.save_episode_facts(episode.id, episode_facts)
    click.echo(f"✓ Saved episode facts: {facts_manager.get_episode_facts_path(episode.id)}")

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


@main.command("clean-transcript-v2")
@click.option("--dry-run", "-d", is_flag=True, help="Show what would be processed")
@click.option("--max-episodes", "-m", default=5, help="Maximum episodes to process")
@click.option("--force", "-f", is_flag=True, help="Re-process even if clean transcript exists")
@click.option("--stream", "-s", is_flag=True, help="Stream LLM output in real-time")
@click.pass_context
def clean_transcript_v2(ctx, dry_run, max_episodes, force, stream):
    """Clean transcripts using facts-based v2 approach"""
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

    for podcast, episode, transcript_path in transcripts_to_clean[:max_episodes]:
        click.echo(f"\n📻 {podcast.title}")
        click.echo(f"🎧 {episode.title}")
        click.echo("─" * 50)

        try:
            # Load transcript
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)

            # Generate output path
            base_name = transcript_path.stem
            if base_name.endswith("_transcript"):
                base_name = base_name[: -len("_transcript")]
            cleaned_filename = f"{base_name}_cleaned.md"
            cleaned_path = path_manager.clean_transcripts_dir() / cleaned_filename

            # Create streaming callback if enabled
            stream_callback = None
            if stream:
                import sys

                def stream_callback(chunk: str) -> None:
                    """Print LLM output chunks in real-time."""
                    sys.stdout.write(chunk)
                    sys.stdout.flush()

            # Clean using v2 approach
            result = cleaning_processor.clean_transcript_v2(
                transcript_data=transcript_data,
                podcast_title=podcast.title,
                podcast_description=podcast.description,
                episode_title=episode.title,
                episode_description=episode.description,
                episode_id=episode.id,
                output_path=str(cleaned_path),
                path_manager=path_manager,
                on_stream_chunk=stream_callback,
            )

            # Add newline after streaming completes
            if stream:
                click.echo("")  # End the streamed output with newline

            if result:
                # Update feed manager
                feed_manager.mark_episode_processed(
                    str(podcast.rss_url),
                    episode.external_id,
                    raw_transcript_path=transcript_path.name,
                    clean_transcript_path=cleaned_filename,
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

    click.echo(CLIFormatter.format_header("thestill.ai Status"))

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
    click.echo(f"    ✓ Cleaned (fully processed):    {stats.episodes_cleaned}")
    click.echo("")
    click.echo(
        f"  Summary: {stats.episodes_cleaned}/{stats.episodes_total} fully processed ({stats.episodes_unprocessed} in progress)"
    )


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
@click.pass_context
def transcribe(ctx, audio_path, downsample, podcast_id, episode_id, max_episodes, dry_run):
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
            )
        except ImportError as e:
            click.echo(f"❌ {e}", err=True)
            click.echo("   Install with: pip install google-cloud-speech google-cloud-storage", err=True)
            ctx.exit(1)
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

            # Prepare cleaning config if enabled
            cleaning_config = None
            if config.enable_transcript_cleaning:
                cleaning_config = {
                    "provider": config.cleaning_provider,
                    "model": config.cleaning_model,
                    "chunk_size": config.cleaning_chunk_size,
                    "overlap_pct": config.cleaning_overlap_pct,
                    "extract_entities": config.cleaning_extract_entities,
                    "base_url": config.ollama_base_url,
                    "api_key": config.openai_api_key,
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

    # Mode 2: Batch transcription of downloaded episodes
    # Use shared services from context
    podcast_service = ctx.obj.podcast_service
    feed_manager = ctx.obj.feed_manager

    # Validate episode_id requires podcast_id
    if episode_id and not podcast_id:
        click.echo("❌ --episode-id requires --podcast-id", err=True)
        ctx.exit(1)

    click.echo("🔍 Looking for episodes to transcribe...")

    # Get episodes that need transcription (sorted by pub_date, newest first)
    episodes_to_transcribe = feed_manager.get_downloaded_episodes(str(config.storage_path))

    if not episodes_to_transcribe:
        click.echo("✓ No episodes found that need transcription")
        return

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)

        episodes_to_transcribe = [(p, ep) for p, ep in episodes_to_transcribe if str(p.rss_url) == str(podcast.rss_url)]

        if not episodes_to_transcribe:
            click.echo(f"✓ No episodes need transcription for podcast: {podcast.title}")
            return

        # Filter by episode_id if specified
        if episode_id:
            target_episode = podcast_service.get_episode(podcast_id, episode_id)
            if not target_episode:
                click.echo(f"❌ Episode not found: {episode_id}", err=True)
                ctx.exit(1)

            # Filter to only the specific episode
            episodes_to_transcribe = [
                (p, ep) for p, ep in episodes_to_transcribe if ep.external_id == target_episode.external_id
            ]

            if not episodes_to_transcribe:
                click.echo(f"✓ Episode already transcribed: {target_episode.title}")
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
        return

    # Transcribe episodes
    transcribed_count = 0
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

                # Determine output path
                output_filename = f"{audio_file.stem}_transcript.json"
                output = str(config.path_manager.raw_transcript_file(output_filename))

                # Transcribe
                click.echo("📝 Transcribing...")

                # Prepare cleaning config if enabled
                cleaning_config = None
                if config.enable_transcript_cleaning:
                    cleaning_config = {
                        "provider": config.cleaning_provider,
                        "model": config.cleaning_model,
                        "chunk_size": config.cleaning_chunk_size,
                        "overlap_pct": config.cleaning_overlap_pct,
                        "extract_entities": config.cleaning_extract_entities,
                        "base_url": config.ollama_base_url,
                        "api_key": config.openai_api_key,
                    }

                transcript_data = transcriber.transcribe_audio(
                    transcription_audio_path,
                    output,
                    clean_transcript=config.enable_transcript_cleaning,
                    cleaning_config=cleaning_config,
                )

                if transcript_data:
                    # Mark episode as having transcript
                    feed_manager.mark_episode_processed(
                        str(podcast.rss_url), episode.external_id, raw_transcript_path=output_filename
                    )
                    transcribed_count += 1
                    click.echo("✅ Transcription complete!")
                else:
                    click.echo("❌ Transcription failed")

            except Exception as e:
                click.echo(f"❌ Error during transcription: {e}")
                import traceback

                traceback.print_exc()
                continue

    total_time = time.time() - start_time
    click.echo("\n🎉 Transcription complete!")
    click.echo(f"✓ {transcribed_count} episode(s) transcribed in {total_time:.1f} seconds")


@main.command()
@click.argument("transcript_path", type=click.Path(exists=True))
@click.option("--add-timestamps/--no-timestamps", default=True, help="Add timestamps to sections")
@click.option("--audio-url", default="", help="Base URL for audio deep links")
@click.option("--speaker-map", default="{}", help="JSON dict of speaker name corrections")
@click.option("--table-layout/--no-table-layout", default=True, help="Use table layout for ads")
@click.option("--output", "-o", help="Output path (defaults to transcript_path with _processed suffix)")
@click.pass_context
def postprocess(ctx, transcript_path, add_timestamps, audio_url, speaker_map, table_layout, output):
    """Post-process a transcript with enhanced LLM processing"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config

    # Load transcript and parse speaker map
    import json

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_data = json.load(f)

    try:
        speaker_map_dict = json.loads(speaker_map)
    except (json.JSONDecodeError, ValueError):
        speaker_map_dict = {}

    # Create config
    post_config = PostProcessorConfig(
        add_timestamps=add_timestamps,
        make_audio_links=bool(audio_url),
        audio_base_url=audio_url,
        speaker_map=speaker_map_dict,
        table_layout_for_snappy_sections=table_layout,
    )

    # Determine output path
    if not output:
        transcript_path_obj = Path(transcript_path)
        output = str(transcript_path_obj.parent / f"{transcript_path_obj.stem}_processed")

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
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    # Process
    post_processor = EnhancedPostProcessor(llm_provider)
    click.echo(f"🔄 Post-processing transcript with {llm_provider.get_model_name()}...")

    try:
        _ = post_processor.process_transcript(transcript_data, post_config, output)
        click.echo("✅ Post-processing complete!")
        click.echo(f"📄 Output saved to: {output}.md and {output}.json")
    except Exception as e:
        click.echo(f"❌ Error during post-processing: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument("transcript_path", type=click.Path(exists=True))
@click.option("--output", "-o", help="Output path for evaluation report")
@click.pass_context
def evaluate_transcript(ctx, transcript_path, output):
    """Evaluate the quality of a raw transcript"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config

    # Load transcript
    import json

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_data = json.load(f)

    # Determine output path
    if not output:
        transcript_path_obj = Path(transcript_path)
        output = str(transcript_path_obj.parent / f"{transcript_path_obj.stem}_evaluation.json")

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
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    # Evaluate
    evaluator = TranscriptEvaluator(llm_provider)
    click.echo(f"📊 Evaluating transcript quality with {llm_provider.get_model_name()}...")

    try:
        evaluation = evaluator.evaluate(transcript_data, output)
        print_evaluation_summary(evaluation, "transcript")
        click.echo(f"📄 Detailed report saved to: {output}")
    except Exception as e:
        click.echo(f"❌ Error during evaluation: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument("processed_path", type=click.Path(exists=True))
@click.option("--original", help="Path to original transcript for comparison")
@click.option("--output", "-o", help="Output path for evaluation report")
@click.pass_context
def evaluate_postprocess(ctx, processed_path, original, output):
    """Evaluate the quality of a post-processed transcript"""
    if ctx.obj is None:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)

    config = ctx.obj.config

    # Load processed content
    import json

    with open(processed_path, "r", encoding="utf-8") as f:
        if processed_path.endswith(".json"):
            processed_data = json.load(f)
        else:
            # If it's markdown, wrap it as content
            processed_data = {"full_output": f.read()}

    # Load original if provided
    original_data = None
    if original:
        with open(original, "r", encoding="utf-8") as f:
            original_data = json.load(f)

    # Determine output path
    if not output:
        processed_path_obj = Path(processed_path)
        output = str(processed_path_obj.parent / f"{processed_path_obj.stem}_evaluation.json")

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
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
        )
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    # Evaluate
    evaluator = PostProcessorEvaluator(llm_provider)
    click.echo(f"📊 Evaluating post-processing quality with {llm_provider.get_model_name()}...")

    try:
        evaluation = evaluator.evaluate(processed_data, original_data, output)
        print_evaluation_summary(evaluation, "post-processor")
        click.echo(f"📄 Detailed report saved to: {output}")
    except Exception as e:
        click.echo(f"❌ Error during evaluation: {e}", err=True)
        ctx.exit(1)


if __name__ == "__main__":
    main()
