import click
import time
from pathlib import Path

try:
    from .utils.config import load_config
    from .core.feed_manager import PodcastFeedManager
    from .core.audio_downloader import AudioDownloader
    from .core.audio_preprocessor import AudioPreprocessor
    from .core.transcriber import WhisperTranscriber, WhisperXTranscriber
    from .core.llm_processor import LLMProcessor
    from .core.post_processor import EnhancedPostProcessor, PostProcessorConfig
    from .core.evaluator import TranscriptEvaluator, PostProcessorEvaluator, print_evaluation_summary
    from .core.llm_provider import create_llm_provider
except ImportError:
    from utils.config import load_config
    from core.feed_manager import PodcastFeedManager
    from core.audio_downloader import AudioDownloader
    from core.audio_preprocessor import AudioPreprocessor
    from core.transcriber import WhisperTranscriber, WhisperXTranscriber
    from core.llm_processor import LLMProcessor
    from core.post_processor import EnhancedPostProcessor, PostProcessorConfig
    from core.evaluator import TranscriptEvaluator, PostProcessorEvaluator, print_evaluation_summary
    from core.llm_provider import create_llm_provider


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
            ollama_model=config.ollama_model
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
            if episode.transcript_path:
                transcript_path = Path(episode.transcript_path)
                if not transcript_path.exists():
                    continue  # Skip if transcript file doesn't exist

                # Check if summary file exists (not just if path is set)
                summary_exists = False
                if episode.summary_path:
                    summary_path = Path(episode.summary_path)
                    summary_exists = summary_path.exists()

                # Only process if transcript exists but summary doesn't
                if not summary_exists:
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
            cleaned_path = config.summaries_path / cleaned_filename

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
                cleaned_transcript = CleanedTranscript(
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
                feed_manager.mark_episode_processed(
                    str(podcast.rss_url),
                    episode.guid,
                    str(transcript_path),
                    str(cleaned_path.with_suffix('.json'))
                )

                total_processed += 1
                click.echo(f"✅ Transcript cleaned successfully!")
                click.echo(f"🔧 Corrections applied: {len(result['corrections'])}")
                click.echo(f"👥 Speakers identified: {len(result['speaker_mapping'])}")

        except Exception as e:
            click.echo(f"❌ Error cleaning transcript: {e}")
            import traceback
            traceback.print_exc()
            continue

    total_time = time.time() - start_time
    click.echo(f"\n🎉 Processing complete!")
    click.echo(f"✓ {total_processed} transcripts cleaned in {total_time:.1f} seconds")


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
    click.echo(f"  Speaker diarization: {'✓ Enabled' if config.enable_diarization else '✗ Disabled'}")
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


@main.command()
@click.argument('audio_path', type=click.Path(exists=True))
@click.option('--output', '-o', help='Output path for transcript JSON')
@click.option('--skip-preprocessing', is_flag=True, help='Skip audio preprocessing/downsampling')
@click.pass_context
def transcribe(ctx, audio_path, output, skip_preprocessing):
    """Transcribe an audio file to JSON transcript"""
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
        click.echo(f"🎤 Using WhisperX with speaker diarization enabled")
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

    # Determine output path
    if not output:
        audio_path_obj = Path(audio_path)
        output = str(config.transcripts_path / f"{audio_path_obj.stem}_transcript.json")

    try:
        # Preprocess audio if needed
        transcription_audio_path = audio_path
        preprocessed_audio_path = None

        if not skip_preprocessing:
            click.echo("🔧 Preprocessing audio for optimal transcription...")
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
            click.echo(f"✅ Transcription complete!")
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

    # Load transcript
    import json
    with open(transcript_path, 'r', encoding='utf-8') as f:
        transcript_data = json.load(f)

    # Parse speaker map
    import json as json_module
    try:
        speaker_map_dict = json_module.loads(speaker_map)
    except:
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
            ollama_model=config.ollama_model
        )
    except Exception as e:
        click.echo(f"❌ Failed to initialize LLM provider: {e}", err=True)
        ctx.exit(1)

    # Process
    post_processor = EnhancedPostProcessor(llm_provider)
    click.echo(f"🔄 Post-processing transcript with {llm_provider.get_model_name()}...")

    try:
        result = post_processor.process_transcript(transcript_data, post_config, output)
        click.echo(f"✅ Post-processing complete!")
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
            ollama_model=config.ollama_model
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
            ollama_model=config.ollama_model
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