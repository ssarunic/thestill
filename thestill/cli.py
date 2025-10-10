import click
import time
from pathlib import Path

try:
    from .utils.config import load_config
    from .utils.logger import setup_logger
    from .services import PodcastService, StatsService
    from .core.feed_manager import PodcastFeedManager
    from .core.audio_downloader import AudioDownloader
    from .core.audio_preprocessor import AudioPreprocessor
    from .core.transcriber import WhisperTranscriber, WhisperXTranscriber
    from .core.post_processor import EnhancedPostProcessor, PostProcessorConfig
    from .core.evaluator import TranscriptEvaluator, PostProcessorEvaluator, print_evaluation_summary
    from .core.llm_provider import create_llm_provider
except ImportError:
    from utils.config import load_config
    from utils.logger import setup_logger
    from services import PodcastService, StatsService
    from core.feed_manager import PodcastFeedManager
    from core.audio_downloader import AudioDownloader
    from core.audio_preprocessor import AudioPreprocessor
    from core.transcriber import WhisperTranscriber, WhisperXTranscriber
    from core.post_processor import EnhancedPostProcessor, PostProcessorConfig
    from core.evaluator import TranscriptEvaluator, PostProcessorEvaluator, print_evaluation_summary
    from core.llm_provider import create_llm_provider


@click.group()
@click.option('--config', '-c', help='Path to config file')
@click.pass_context
def main(ctx, config):
    """thestill.ai - Automated podcast transcription and summarization"""
    ctx.ensure_object(dict)

    # Initialize logging to stderr (important for MCP server compatibility)
    setup_logger("thestill", log_level="INFO", console_output=True)

    try:
        ctx.obj['config'] = load_config(config)
        click.echo("✓ Configuration loaded successfully")
    except Exception as e:
        click.echo(f"❌ Configuration error: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument('rss_url')
@click.pass_context
def add(ctx, rss_url):
    """Add a podcast RSS feed"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    podcast_service = PodcastService(str(config.storage_path))

    podcast = podcast_service.add_podcast(rss_url)
    if podcast:
        click.echo(f"✓ Podcast added: {podcast.title}")
    else:
        click.echo("❌ Failed to add podcast or podcast already exists", err=True)


@main.command()
@click.argument('podcast_id')
@click.pass_context
def remove(ctx, podcast_id):
    """Remove a podcast by RSS URL or index number"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    podcast_service = PodcastService(str(config.storage_path))

    if podcast_service.remove_podcast(podcast_id):
        click.echo("✓ Podcast removed")
    else:
        click.echo("❌ Podcast not found", err=True)


@main.command()
@click.pass_context
def list(ctx):
    """List all tracked podcasts"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    podcast_service = PodcastService(str(config.storage_path))

    podcasts = podcast_service.list_podcasts()

    if not podcasts:
        click.echo("No podcasts tracked yet. Use 'thestill add <rss_url>' to add some!")
        return

    click.echo(f"\n📻 Tracked Podcasts ({len(podcasts)}):")
    click.echo("─" * 50)

    for podcast in podcasts:
        click.echo(f"{podcast.index}. {podcast.title}")
        click.echo(f"   RSS: {podcast.rss_url}")
        if podcast.last_processed:
            click.echo(f"   Last processed: {podcast.last_processed.strftime('%Y-%m-%d %H:%M')}")
        click.echo(f"   Episodes: {podcast.episodes_processed}/{podcast.episodes_count} processed")
        click.echo()


@main.command()
@click.option('--podcast-id', help='Download from specific podcast (index or RSS URL)')
@click.option('--max-episodes', '-m', type=int, help='Maximum episodes to download per podcast')
@click.option('--dry-run', '-d', is_flag=True, help='Show what would be downloaded without downloading')
@click.pass_context
def download(ctx, podcast_id, max_episodes, dry_run):
    """Download audio files for new episodes from tracked podcasts"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    feed_manager = PodcastFeedManager(str(config.storage_path))
    downloader = AudioDownloader(str(config.path_manager.original_audio_dir()))
    podcast_service = PodcastService(str(config.storage_path))

    # Check for new episodes
    click.echo("🔍 Checking for new episodes...")
    new_episodes = feed_manager.get_new_episodes()

    if not new_episodes:
        click.echo("✓ No new episodes found")
        return

    # Filter by podcast_id if specified
    if podcast_id:
        podcast = podcast_service.get_podcast(podcast_id)
        if not podcast:
            click.echo(f"❌ Podcast not found: {podcast_id}", err=True)
            ctx.exit(1)

        # Filter new_episodes to only include the specified podcast
        new_episodes = [(p, eps) for p, eps in new_episodes if str(p.rss_url) == str(podcast.rss_url)]

        if not new_episodes:
            click.echo(f"✓ No new episodes found for podcast: {podcast.title}")
            return

    # Apply max_episodes limit
    episodes_to_download = []
    for podcast, episodes in new_episodes:
        if max_episodes:
            episodes = episodes[:max_episodes]
        episodes_to_download.append((podcast, episodes))

    # Count total episodes
    total_episodes = sum(len(eps) for _, eps in episodes_to_download)
    click.echo(f"📥 Found {total_episodes} new episode(s) to download")

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

    for podcast, episodes in episodes_to_download:
        click.echo(f"\n📻 {podcast.title}")
        click.echo("─" * 50)

        for episode in episodes:
            click.echo(f"\n🎧 {episode.title}")

            # Check if already downloaded
            if episode.audio_path:
                audio_file = config.path_manager.original_audio_file(episode.audio_path)
                if audio_file.exists():
                    click.echo("⏭️  Already downloaded, skipping")
                    continue

            try:
                audio_path = downloader.download_episode(episode, podcast.title)

                if audio_path:
                    # Store just the filename
                    audio_filename = Path(audio_path).name

                    feed_manager.mark_episode_downloaded(
                        str(podcast.rss_url),
                        episode.guid,
                        audio_filename
                    )
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


@main.command()
@click.option('--podcast-id', help='Podcast ID (RSS URL or index) to downsample')
@click.option('--max-episodes', '-m', type=int, help='Maximum episodes to downsample')
@click.option('--dry-run', '-d', is_flag=True, help='Preview what would be downsampled')
@click.pass_context
def downsample(ctx, podcast_id, max_episodes, dry_run):
    """Downsample downloaded audio to 16kHz, 16-bit, mono WAV format"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    feed_manager = PodcastFeedManager(str(config.storage_path))
    preprocessor = AudioPreprocessor()
    podcast_service = PodcastService(str(config.storage_path))

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

        episodes_to_downsample = [(p, eps) for p, eps in episodes_to_downsample
                                  if str(p.rss_url) == str(podcast.rss_url)]

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

    for podcast, episodes in episodes_to_downsample:
        click.echo(f"\n📻 {podcast.title}")
        click.echo("─" * 50)

        for episode in episodes:
            click.echo(f"\n🎧 {episode.title}")

            try:
                # Build paths
                original_audio_file = config.path_manager.original_audio_file(episode.audio_path)

                if not original_audio_file.exists():
                    click.echo(f"❌ Original audio file not found: {episode.audio_path}")
                    continue

                # Downsample
                click.echo("🔧 Downsampling to 16kHz, 16-bit, mono WAV...")
                downsampled_path = preprocessor.downsample_audio(
                    str(original_audio_file),
                    str(config.path_manager.downsampled_audio_dir())
                )

                if downsampled_path:
                    # Store just the filename
                    downsampled_filename = Path(downsampled_path).name

                    feed_manager.mark_episode_downsampled(
                        str(podcast.rss_url),
                        episode.guid,
                        downsampled_filename
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
@click.option('--dry-run', '-d', is_flag=True, help='Show what would be processed without actually processing')
@click.option('--max-episodes', '-m', default=5, help='Maximum episodes to process per podcast')
@click.pass_context
def clean_transcript(ctx, dry_run, max_episodes):
    """Clean existing transcripts with LLM post-processing"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    from .core.transcript_cleaning_processor import TranscriptCleaningProcessor
    from .models.podcast import CleanedTranscript
    from datetime import datetime
    import json

    feed_manager = PodcastFeedManager(str(config.storage_path))

    # Create LLM provider based on configuration
    try:
        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.llm_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model
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

    for podcast, episode, transcript_path in transcripts_to_clean[:max_episodes]:
        click.echo(f"\n📻 {podcast.title}")
        click.echo(f"🎧 {episode.title}")
        click.echo("─" * 50)

        try:
            # Load transcript
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript_data = json.load(f)

            # Clean transcript with context
            cleaned_filename = f"{transcript_path.stem}_cleaned"
            cleaned_path = config.path_manager.clean_transcripts_dir() / cleaned_filename

            result = cleaning_processor.clean_transcript(
                transcript_data=transcript_data,
                podcast_title=podcast.title,
                podcast_description=podcast.description,
                episode_title=episode.title,
                episode_description=episode.description,
                output_path=str(cleaned_path),
                save_corrections=True
            )

            if result:
                # Create CleanedTranscript model and save
                _ = CleanedTranscript(
                    episode_guid=episode.guid,
                    episode_title=episode.title,
                    podcast_title=podcast.title,
                    corrections=result['corrections'],
                    speaker_mapping=result['speaker_mapping'],
                    cleaned_markdown=result['cleaned_markdown'],
                    processing_time=result['processing_time'],
                    created_at=datetime.now()
                )

                # Update feed manager to mark as processed
                # The TranscriptCleaningProcessor removes '_transcript_cleaned' and '_transcript' suffixes
                # So we need to match the actual filename it creates: {episode_id}.md
                episode_id = cleaned_path.stem.replace('_transcript_cleaned', '').replace('_transcript', '')
                cleaned_md_filename = f"{episode_id}.md"
                feed_manager.mark_episode_processed(
                    str(podcast.rss_url),
                    episode.guid,
                    raw_transcript_path=transcript_path.name,  # Just the raw transcript filename
                    clean_transcript_path=cleaned_md_filename  # Just the cleaned MD filename
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


@main.command()
@click.pass_context
def status(ctx):
    """Show system status and statistics"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    stats_service = StatsService(str(config.storage_path))

    click.echo("📊 thestill.ai Status")
    click.echo("═" * 30)

    # Get statistics from service
    stats = stats_service.get_stats()

    # Storage info
    click.echo(f"Storage path: {stats.storage_path}")
    click.echo(f"Audio files: {stats.audio_files_count} files")
    click.echo(f"Transcripts available: {stats.transcripts_available} files")

    # Configuration
    click.echo("\nConfiguration:")
    click.echo(f"  Whisper model: {config.whisper_model}")
    click.echo(f"  Speaker diarization: {'✓ Enabled' if config.enable_diarization else '✗ Disabled'}")
    click.echo(f"  LLM model: {config.llm_model}")
    click.echo(f"  Max workers: {config.max_workers}")

    # Podcast stats
    click.echo("\nPodcast Statistics:")
    click.echo(f"  Tracked podcasts: {stats.podcasts_tracked}")
    click.echo(f"  Total episodes: {stats.episodes_total}")
    click.echo(f"  Processed episodes: {stats.episodes_processed}")
    click.echo(f"  Unprocessed episodes: {stats.episodes_unprocessed}")


@main.command()
@click.pass_context
def cleanup(ctx):
    """Clean up old audio files"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    downloader = AudioDownloader(str(config.path_manager.original_audio_dir()))

    click.echo(f"🧹 Cleaning up files older than {config.cleanup_days} days...")
    downloader.cleanup_old_files(config.cleanup_days)
    click.echo("✓ Cleanup complete")


@main.command()
@click.argument('audio_path', type=click.Path(exists=True), required=False)
@click.option('--downsample', is_flag=True, help='Enable audio downsampling (16kHz, mono, 16-bit)')
@click.option('--podcast-id', help='Transcribe episodes from specific podcast (index or RSS URL)')
@click.option('--episode-id', help='Transcribe specific episode (requires --podcast-id)')
@click.option('--max-episodes', '-m', type=int, help='Maximum episodes to transcribe')
@click.pass_context
def transcribe(ctx, audio_path, downsample, podcast_id, episode_id, max_episodes):
    """Transcribe audio files to JSON transcripts.

    Without arguments: Transcribes all downloaded episodes that need transcription.
    With audio_path: Transcribes a specific audio file (standalone mode).
    """
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    preprocessor = AudioPreprocessor()

    # Initialize the appropriate transcriber based on config settings
    if config.transcription_model.lower() == 'parakeet':
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
            diarization_model=config.diarization_model
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
                    "api_key": config.openai_api_key
                }

            transcript_data = transcriber.transcribe_audio(
                transcription_audio_path,
                output,
                clean_transcript=config.enable_transcript_cleaning,
                cleaning_config=cleaning_config
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
            if 'preprocessed_audio_path' in locals() and preprocessed_audio_path and preprocessed_audio_path != audio_path:
                preprocessor.cleanup_preprocessed_file(preprocessed_audio_path)
            ctx.exit(1)
        return

    # Mode 2: Batch transcription of downloaded episodes
    feed_manager = PodcastFeedManager(str(config.storage_path))
    podcast_service = PodcastService(str(config.storage_path))

    # Validate episode_id requires podcast_id
    if episode_id and not podcast_id:
        click.echo("❌ --episode-id requires --podcast-id", err=True)
        ctx.exit(1)

    click.echo("🔍 Looking for episodes to transcribe...")

    # Get episodes that need transcription
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

        episodes_to_transcribe = [(p, eps) for p, eps in episodes_to_transcribe
                                  if str(p.rss_url) == str(podcast.rss_url)]

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
            episodes_to_transcribe = [(p, [ep for ep in eps if ep.guid == target_episode.guid])
                                     for p, eps in episodes_to_transcribe]
            episodes_to_transcribe = [(p, eps) for p, eps in episodes_to_transcribe if eps]

            if not episodes_to_transcribe:
                click.echo(f"✓ Episode already transcribed: {target_episode.title}")
                return

    # Apply max_episodes limit
    if max_episodes:
        total = 0
        filtered = []
        for podcast, episodes in episodes_to_transcribe:
            remaining = max_episodes - total
            if remaining <= 0:
                break
            filtered.append((podcast, episodes[:remaining]))
            total += len(episodes[:remaining])
        episodes_to_transcribe = filtered

    # Count total episodes
    total_count = sum(len(eps) for _, eps in episodes_to_transcribe)
    click.echo(f"📝 Found {total_count} episode(s) to transcribe")

    # Transcribe episodes
    transcribed_count = 0
    start_time = time.time()

    for podcast, episodes in episodes_to_transcribe:
        click.echo(f"\n📻 {podcast.title}")
        click.echo("─" * 50)

        for episode in episodes:
            click.echo(f"\n🎧 {episode.title}")

            try:
                # Only use downsampled audio - fail if not available
                if not episode.downsampled_audio_path:
                    click.echo("❌ No downsampled audio available for this episode")
                    click.echo("   Run 'thestill download' again to generate downsampled audio")
                    continue

                audio_file = config.path_manager.downsampled_audio_file(episode.downsampled_audio_path)

                if not audio_file.exists():
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
                        "api_key": config.openai_api_key
                    }

                transcript_data = transcriber.transcribe_audio(
                    transcription_audio_path,
                    output,
                    clean_transcript=config.enable_transcript_cleaning,
                    cleaning_config=cleaning_config
                )

                if transcript_data:
                    # Mark episode as having transcript
                    feed_manager.mark_episode_processed(
                        str(podcast.rss_url),
                        episode.guid,
                        raw_transcript_path=output_filename
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
@click.argument('transcript_path', type=click.Path(exists=True))
@click.option('--add-timestamps/--no-timestamps', default=True, help='Add timestamps to sections')
@click.option('--audio-url', default='', help='Base URL for audio deep links')
@click.option('--speaker-map', default='{}', help='JSON dict of speaker name corrections')
@click.option('--table-layout/--no-table-layout', default=True, help='Use table layout for ads')
@click.option('--output', '-o', help='Output path (defaults to transcript_path with _processed suffix)')
@click.pass_context
def postprocess(ctx, transcript_path, add_timestamps, audio_url, speaker_map, table_layout, output):
    """Post-process a transcript with enhanced LLM processing"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    # Load transcript and parse speaker map
    import json
    with open(transcript_path, 'r', encoding='utf-8') as f:
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
        table_layout_for_snappy_sections=table_layout
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
            openai_model=config.llm_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model
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
@click.argument('transcript_path', type=click.Path(exists=True))
@click.option('--output', '-o', help='Output path for evaluation report')
@click.pass_context
def evaluate_transcript(ctx, transcript_path, output):
    """Evaluate the quality of a raw transcript"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    # Load transcript
    import json
    with open(transcript_path, 'r', encoding='utf-8') as f:
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
            openai_model=config.llm_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model
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
@click.argument('processed_path', type=click.Path(exists=True))
@click.option('--original', help='Path to original transcript for comparison')
@click.option('--output', '-o', help='Output path for evaluation report')
@click.pass_context
def evaluate_postprocess(ctx, processed_path, original, output):
    """Evaluate the quality of a post-processed transcript"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    # Load processed content
    import json
    with open(processed_path, 'r', encoding='utf-8') as f:
        if processed_path.endswith('.json'):
            processed_data = json.load(f)
        else:
            # If it's markdown, wrap it as content
            processed_data = {"full_output": f.read()}

    # Load original if provided
    original_data = None
    if original:
        with open(original, 'r', encoding='utf-8') as f:
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
            openai_model=config.llm_model,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model
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


if __name__ == '__main__':
    main()
