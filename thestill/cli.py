import click
import time
from pathlib import Path

try:
    from .utils.config import load_config
    from .core.feed_manager import PodcastFeedManager
    from .core.audio_downloader import AudioDownloader
    from .core.transcriber import WhisperTranscriber
    from .core.llm_processor import LLMProcessor
except ImportError:
    from utils.config import load_config
    from core.feed_manager import PodcastFeedManager
    from core.audio_downloader import AudioDownloader
    from core.transcriber import WhisperTranscriber
    from core.llm_processor import LLMProcessor


@click.group()
@click.option('--config', '-c', help='Path to config file')
@click.pass_context
def main(ctx, config):
    """thestill.ai - Automated podcast transcription and summarization"""
    ctx.ensure_object(dict)
    try:
        ctx.obj['config'] = load_config(config)
        print("✓ Configuration loaded successfully")
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
    feed_manager = PodcastFeedManager(str(config.storage_path))

    if feed_manager.add_podcast(rss_url):
        click.echo(f"✓ Podcast added: {rss_url}")
    else:
        click.echo(f"❌ Failed to add podcast or podcast already exists", err=True)


@main.command()
@click.argument('rss_url')
@click.pass_context
def remove(ctx, rss_url):
    """Remove a podcast RSS feed"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    feed_manager = PodcastFeedManager(str(config.storage_path))

    if feed_manager.remove_podcast(rss_url):
        click.echo(f"✓ Podcast removed: {rss_url}")
    else:
        click.echo(f"❌ Podcast not found", err=True)


@main.command()
@click.pass_context
def list(ctx):
    """List all tracked podcasts"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    feed_manager = PodcastFeedManager(str(config.storage_path))

    podcasts = feed_manager.list_podcasts()

    if not podcasts:
        click.echo("No podcasts tracked yet. Use 'thestill add <rss_url>' to add some!")
        return

    click.echo(f"\n📻 Tracked Podcasts ({len(podcasts)}):")
    click.echo("─" * 50)

    for i, podcast in enumerate(podcasts, 1):
        click.echo(f"{i}. {podcast.title}")
        click.echo(f"   RSS: {podcast.rss_url}")
        if podcast.last_processed:
            click.echo(f"   Last processed: {podcast.last_processed.strftime('%Y-%m-%d %H:%M')}")
        processed_count = sum(1 for ep in podcast.episodes if ep.processed)
        click.echo(f"   Episodes processed: {processed_count}")
        click.echo()


@main.command()
@click.option('--dry-run', '-d', is_flag=True, help='Show what would be processed without actually processing')
@click.option('--max-episodes', '-m', default=5, help='Maximum episodes to process per podcast')
@click.pass_context
def process(ctx, dry_run, max_episodes):
    """Check for new episodes and process them"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    feed_manager = PodcastFeedManager(str(config.storage_path))
    downloader = AudioDownloader(str(config.audio_path))
    transcriber = WhisperTranscriber(config.whisper_model, config.whisper_device)
    processor = LLMProcessor(config.openai_api_key, config.llm_model)

    click.echo("🔍 Checking for new episodes...")
    new_episodes = feed_manager.get_new_episodes()

    if not new_episodes:
        click.echo("✓ No new episodes found")
        return

    total_episodes = sum(min(len(episodes), max_episodes) for _, episodes in new_episodes)
    click.echo(f"📥 Found {total_episodes} new episodes to process")

    if dry_run:
        for podcast, episodes in new_episodes:
            click.echo(f"\n{podcast.title}:")
            for episode in episodes[:max_episodes]:
                click.echo(f"  • {episode.title}")
        click.echo("\n(Use --dry-run=false to actually process)")
        return

    total_processed = 0
    start_time = time.time()

    for podcast, episodes in new_episodes:
        click.echo(f"\n📻 Processing {podcast.title}")
        click.echo("─" * 50)

        for episode in episodes[:max_episodes]:
            click.echo(f"\n🎧 Episode: {episode.title}")

            try:
                # Step 1: Download audio
                audio_path = downloader.download_episode(episode, podcast.title)
                if not audio_path:
                    click.echo("❌ Download failed, skipping episode")
                    continue

                # Step 2: Transcribe
                transcript_filename = f"{Path(audio_path).stem}_transcript.json"
                transcript_path = config.transcripts_path / transcript_filename

                transcript_data = transcriber.transcribe_audio(
                    audio_path,
                    str(transcript_path)
                )

                if not transcript_data:
                    click.echo("❌ Transcription failed, skipping episode")
                    continue

                # Step 3: Process with LLM
                transcript_text = transcriber.get_transcript_text(transcript_data)

                summary_filename = f"{Path(audio_path).stem}_summary.json"
                summary_path = config.summaries_path / summary_filename

                processed_content = processor.process_transcript(
                    transcript_text,
                    episode.guid,
                    str(summary_path)
                )

                if processed_content:
                    feed_manager.mark_episode_processed(
                        str(podcast.rss_url),
                        episode.guid,
                        str(transcript_path),
                        str(summary_path)
                    )

                    total_processed += 1
                    click.echo(f"✅ Episode processed successfully!")

                    # Show brief summary
                    click.echo(f"📝 Summary: {processed_content.summary[:200]}...")
                    click.echo(f"💬 Quotes found: {len(processed_content.quotes)}")

            except Exception as e:
                click.echo(f"❌ Error processing episode: {e}")
                continue

    total_time = time.time() - start_time
    click.echo(f"\n🎉 Processing complete!")
    click.echo(f"✓ {total_processed} episodes processed in {total_time:.1f} seconds")

    if config.cleanup_days > 0:
        click.echo("🧹 Cleaning up old files...")
        downloader.cleanup_old_files(config.cleanup_days)


@main.command()
@click.pass_context
def status(ctx):
    """Show system status and statistics"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']

    click.echo("📊 thestill.ai Status")
    click.echo("═" * 30)

    # Storage info
    click.echo(f"Storage path: {config.storage_path}")
    click.echo(f"Audio files: {len([f for f in config.audio_path.glob('*')])} files")
    click.echo(f"Transcripts: {len([f for f in config.transcripts_path.glob('*.json')])} files")
    click.echo(f"Summaries: {len([f for f in config.summaries_path.glob('*.json')])} files")

    # Configuration
    click.echo(f"\nConfiguration:")
    click.echo(f"  Whisper model: {config.whisper_model}")
    click.echo(f"  LLM model: {config.llm_model}")
    click.echo(f"  Max workers: {config.max_workers}")

    # Podcast stats
    feed_manager = PodcastFeedManager(str(config.storage_path))
    podcasts = feed_manager.list_podcasts()

    total_episodes = sum(len(p.episodes) for p in podcasts)
    processed_episodes = sum(sum(1 for ep in p.episodes if ep.processed) for p in podcasts)

    click.echo(f"\nPodcast Statistics:")
    click.echo(f"  Tracked podcasts: {len(podcasts)}")
    click.echo(f"  Total episodes: {total_episodes}")
    click.echo(f"  Processed episodes: {processed_episodes}")


@main.command()
@click.pass_context
def cleanup(ctx):
    """Clean up old audio files"""
    if ctx.obj is None or 'config' not in ctx.obj:
        click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
        ctx.exit(1)
    config = ctx.obj['config']
    downloader = AudioDownloader(str(config.audio_path))

    click.echo(f"🧹 Cleaning up files older than {config.cleanup_days} days...")
    downloader.cleanup_old_files(config.cleanup_days)
    click.echo("✓ Cleanup complete")


if __name__ == '__main__':
    main()