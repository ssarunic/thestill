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

"""
Google Cloud Speech-to-Text V2 transcriber with Chirp 3 model and speaker diarization.
"""

import json
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydub import AudioSegment

try:
    from google.api_core.client_options import ClientOptions
    from google.cloud import storage
    from google.cloud.speech_v2 import SpeechClient
    from google.cloud.speech_v2.types import cloud_speech
    from google.oauth2 import service_account

    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    GOOGLE_CLOUD_AVAILABLE = False
    # Create dummy module references for type hints when library not available
    SpeechClient = None  # type: ignore
    cloud_speech = None  # type: ignore
    storage = None  # type: ignore
    service_account = None  # type: ignore
    ClientOptions = None  # type: ignore


# Default region for Speech-to-Text V2 API
# Chirp 3 supported regions (GA): us, eu, asia-northeast1, asia-southeast1
# Chirp 3 supported regions (Preview): asia-south1, europe-west2, europe-west3, northamerica-northeast1
DEFAULT_REGION = "us"

# Chirp 3 has a 60-minute limit for BatchRecognize
# We split longer files into chunks with overlap for seamless merging
MAX_CHUNK_DURATION_MS = 10 * 60 * 1000  # 10 minutes in milliseconds (for debugging)
OVERLAP_DURATION_MS = 60 * 1000  # 1 minute overlap for merging


class GoogleCloudTranscriber:
    """
    Google Cloud Speech-to-Text V2 transcriber with Chirp 3 model.

    Features:
    - Uses Chirp 3 model for best-in-class accuracy
    - BatchRecognize for all transcriptions (required for diarization)
    - Built-in speaker diarization
    - Word-level timestamps
    - Automatic punctuation

    Output format matches WhisperTranscriber for compatibility with existing pipeline.

    Note: Chirp 3 and diarization require the V2 API with BatchRecognize.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        storage_bucket: Optional[str] = None,
        enable_diarization: bool = True,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        region: str = DEFAULT_REGION,
    ):
        """
        Initialize Google Cloud Speech V2 transcriber with Chirp 3.

        Args:
            credentials_path: Path to service account JSON key file
            project_id: Google Cloud project ID (required)
            storage_bucket: GCS bucket name for audio files (optional, will auto-create)
            enable_diarization: Enable speaker diarization (only available with BatchRecognize)
            min_speakers: Minimum number of speakers (None = auto-detect)
            max_speakers: Maximum number of speakers (None = auto-detect)
            region: Google Cloud region for Speech API (default: "eu")
        """
        if not GOOGLE_CLOUD_AVAILABLE:
            raise ImportError(
                "Google Cloud Speech-to-Text V2 libraries not installed. "
                "Install with: pip install google-cloud-speech google-cloud-storage"
            )

        if not project_id:
            raise ValueError("project_id is required for Speech-to-Text V2 API")

        self.credentials_path = credentials_path
        self.project_id = project_id
        self.storage_bucket = storage_bucket
        self.enable_diarization = enable_diarization
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.region = region

        self.speech_client = None
        self.storage_client = None
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize Google Cloud clients for V2 API."""
        try:
            # Set up credentials
            credentials = None
            if self.credentials_path:
                credentials = service_account.Credentials.from_service_account_file(self.credentials_path)

            # Initialize Speech V2 client with regional endpoint
            client_options = ClientOptions(api_endpoint=f"{self.region}-speech.googleapis.com")

            if credentials:
                self.speech_client = SpeechClient(credentials=credentials, client_options=client_options)
                self.storage_client = storage.Client(credentials=credentials, project=self.project_id)
            else:
                # Use default credentials (e.g., from GOOGLE_APPLICATION_CREDENTIALS env var)
                self.speech_client = SpeechClient(client_options=client_options)
                try:
                    self.storage_client = storage.Client(project=self.project_id)
                except Exception:
                    print(
                        "WARNING: Google Cloud Storage client not available - "
                        "transcription requires GCS for BatchRecognize"
                    )

            print(f"Google Cloud Speech V2 client initialized " f"(region: {self.region}, model: chirp_3)")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Google Cloud clients: {e}")

    def _configure_bucket_lifecycle(self, bucket) -> None:
        """
        Configure bucket lifecycle to auto-delete temp-audio/ objects after 7 days.

        This prevents orphaned files if the app crashes before cleanup.
        Only applies to objects with prefix 'temp-audio/' - other folders are unaffected.
        """
        bucket.lifecycle_rules = [
            {
                "action": {"type": "Delete"},
                "condition": {"age": 7, "matchesPrefix": ["temp-audio/"]},  # Days since object creation
            }
        ]
        bucket.patch()
        print("Configured bucket lifecycle: auto-delete temp-audio/ after 7 days")

    def _sanitize_filename(self, text: str) -> str:
        """
        Sanitize text for use in file names.

        Replaces spaces with underscores, removes special characters,
        and collapses multiple underscores.
        """
        if not text:
            return ""
        # Replace spaces with underscores, remove non-alphanumeric chars except underscores/hyphens
        sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in text)
        # Collapse multiple underscores and trim
        while "__" in sanitized:
            sanitized = sanitized.replace("__", "_")
        return sanitized.strip("_")

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: str = None,
        language: str = "en-US",
        custom_prompt: str = None,
        preprocess_audio: bool = False,
        clean_transcript: bool = False,
        cleaning_config: Dict = None,
        podcast_title: str = None,
    ) -> Optional[Dict]:
        """
        Transcribe audio file using Chirp 3 with optional speaker diarization.

        For files longer than 30 minutes, the audio is automatically split into
        chunks with 1-minute overlap, transcribed separately, and merged.

        Args:
            audio_path: Path to audio file (must be downsampled 16kHz WAV)
            output_path: Path to save transcript JSON
            language: Language code (e.g., 'en-US', 'hr-HR')
            custom_prompt: Not used for Google Cloud (included for API compatibility)
            preprocess_audio: Not used for Google Cloud (included for API compatibility)
            clean_transcript: Not used for Google Cloud (included for API compatibility)
            cleaning_config: Not used for Google Cloud (included for API compatibility)
            podcast_title: Optional podcast title used as prefix for temp files in GCS

        Returns:
            Transcript dictionary matching Whisper format, or None on error
        """
        try:
            print(f"Starting Google Cloud V2 transcription of: {Path(audio_path).name}")
            start_time = time.time()

            # Load audio to check duration
            audio = AudioSegment.from_file(audio_path)
            duration_ms = len(audio)
            duration_minutes = duration_ms / 1000 / 60

            if duration_ms > MAX_CHUNK_DURATION_MS:
                # Split and transcribe in chunks
                print(
                    f"Audio is {duration_minutes:.1f} minutes - splitting into "
                    f"{MAX_CHUNK_DURATION_MS // 60000}-minute chunks with "
                    f"{OVERLAP_DURATION_MS // 1000}s overlap"
                )
                transcript_data = self._transcribe_chunked(audio, audio_path, language, output_path, podcast_title)
            else:
                # Transcribe directly
                result = self._transcribe_batch(audio_path, language, podcast_title)
                transcript_data = self._format_transcript(result, time.time() - start_time, audio_path)

            processing_time = time.time() - start_time
            transcript_data["processing_time"] = processing_time
            print(f"Transcription completed in {processing_time:.1f} seconds")

            if output_path:
                self._save_transcript(transcript_data, output_path)

            return transcript_data

        except Exception as e:
            print(f"Error transcribing {audio_path}: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _transcribe_chunked(
        self,
        audio: AudioSegment,
        original_path: str,
        language: str,
        output_path: Optional[str] = None,
        podcast_title: Optional[str] = None,
    ) -> Dict:
        """
        Split audio into chunks, transcribe each, and merge results.

        Args:
            audio: Loaded AudioSegment
            original_path: Original audio file path (for metadata)
            language: Language code
            output_path: Path for final transcript (used for debug chunk files)
            podcast_title: Optional podcast title used as prefix for temp files in GCS

        Returns:
            Merged transcript dictionary
        """
        duration_ms = len(audio)
        chunks = []
        chunk_start = 0

        # Calculate chunk boundaries with overlap
        while chunk_start < duration_ms:
            chunk_end = min(chunk_start + MAX_CHUNK_DURATION_MS, duration_ms)
            chunks.append((chunk_start, chunk_end))

            # If we've reached the end of the audio, stop to avoid an infinite loop
            if chunk_end >= duration_ms:
                break

            # Next chunk starts (chunk_duration - overlap) after current start
            # This creates overlap at the end of each chunk
            chunk_start = chunk_end - OVERLAP_DURATION_MS

        print(f"Split into {len(chunks)} chunks")

        # Determine debug output directory (same as final transcript location)
        debug_dir = None
        if output_path:
            debug_dir = Path(output_path).parent / "chunks"
            debug_dir.mkdir(parents=True, exist_ok=True)
            print(f"Debug chunk files will be saved to: {debug_dir}")

        # Create filename prefix from podcast title
        sanitized_title = self._sanitize_filename(podcast_title) if podcast_title else ""
        file_prefix = f"{sanitized_title}_" if sanitized_title else ""

        # Transcribe each chunk
        chunk_transcripts = []
        for i, (start_ms, end_ms) in enumerate(chunks):
            print(
                f"Processing chunk {i + 1}/{len(chunks)} "
                f"({start_ms // 1000 // 60}:{start_ms // 1000 % 60:02d} - "
                f"{end_ms // 1000 // 60}:{end_ms // 1000 % 60:02d})"
            )

            # Extract chunk
            chunk_audio = audio[start_ms:end_ms]

            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = tmp_file.name
                chunk_audio.export(tmp_path, format="wav")

            try:
                # Transcribe chunk
                result = self._transcribe_batch(tmp_path, language, podcast_title)
                transcript = self._format_transcript(result, 0, tmp_path)

                # Save raw chunk transcript (before timestamp adjustment) for debugging
                if debug_dir:
                    raw_chunk_path = debug_dir / f"{file_prefix}chunk_{i+1:02d}_raw.json"
                    with open(raw_chunk_path, "w", encoding="utf-8") as f:
                        json.dump(transcript, f, indent=2, ensure_ascii=False)
                    print(f"  Saved raw chunk transcript: {raw_chunk_path.name}")

                # Adjust timestamps by chunk offset
                offset_seconds = start_ms / 1000
                for segment in transcript["segments"]:
                    segment["start"] += offset_seconds
                    segment["end"] += offset_seconds
                    for word in segment.get("words", []):
                        word["start"] += offset_seconds
                        word["end"] += offset_seconds

                # Save adjusted chunk transcript for debugging
                if debug_dir:
                    adjusted_chunk_path = debug_dir / f"{file_prefix}chunk_{i+1:02d}_adjusted.json"
                    with open(adjusted_chunk_path, "w", encoding="utf-8") as f:
                        json.dump(transcript, f, indent=2, ensure_ascii=False)
                    print(f"  Saved adjusted chunk transcript: {adjusted_chunk_path.name}")

                chunk_transcripts.append(
                    {
                        "transcript": transcript,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                    }
                )
            finally:
                # Clean up temp file
                Path(tmp_path).unlink(missing_ok=True)

        # Merge chunk transcripts
        return self._merge_chunk_transcripts(chunk_transcripts, original_path, language)

    def _merge_chunk_transcripts(
        self,
        chunk_transcripts: List[Dict],
        original_path: str,
        language: str,
    ) -> Dict:
        """
        Merge transcripts from multiple chunks, handling overlap deduplication.

        The overlap region is used to find matching words and avoid duplication.
        We keep words from the first chunk up to the best match point, then
        continue with words from the second chunk.

        Args:
            chunk_transcripts: List of {transcript, start_ms, end_ms} dicts
            original_path: Original audio file path
            language: Detected language

        Returns:
            Merged transcript dictionary
        """
        if not chunk_transcripts:
            return self._empty_transcript(original_path)

        if len(chunk_transcripts) == 1:
            return chunk_transcripts[0]["transcript"]

        # Collect all words with timestamps from all chunks
        all_words = []
        for chunk_data in chunk_transcripts:
            transcript = chunk_data["transcript"]
            for segment in transcript.get("segments", []):
                for word in segment.get("words", []):
                    all_words.append(word)

        # Sort by start time
        all_words.sort(key=lambda w: w["start"])

        # Remove duplicates from overlap regions
        # Words within 0.5 seconds with same text are considered duplicates
        deduplicated_words = []
        for word in all_words:
            is_duplicate = False
            for existing in deduplicated_words[-10:]:  # Check last 10 words
                time_diff = abs(word["start"] - existing["start"])
                if time_diff < 0.5 and word["word"].lower() == existing["word"].lower():
                    is_duplicate = True
                    break
            if not is_duplicate:
                deduplicated_words.append(word)

        # Rebuild segments from deduplicated words
        segments = []
        speakers_detected = set()

        if not deduplicated_words:
            return self._empty_transcript(original_path)

        # Group words by speaker changes
        current_speaker = deduplicated_words[0].get("speaker")
        current_words = []
        current_start = deduplicated_words[0]["start"]
        segment_id = 0

        for word in deduplicated_words:
            speaker = word.get("speaker")
            if speaker:
                speakers_detected.add(speaker)

            if speaker != current_speaker and current_words:
                # Save segment
                segment_text = " ".join(w["word"] for w in current_words)
                segments.append(
                    {
                        "id": segment_id,
                        "start": current_start,
                        "end": current_words[-1]["end"],
                        "text": segment_text,
                        "speaker": current_speaker,
                        "words": current_words,
                    }
                )
                segment_id += 1

                # Start new segment
                current_speaker = speaker
                current_words = []
                current_start = word["start"]

            current_words.append(word)

        # Save final segment
        if current_words:
            segment_text = " ".join(w["word"] for w in current_words)
            segments.append(
                {
                    "id": segment_id,
                    "start": current_start,
                    "end": current_words[-1]["end"],
                    "text": segment_text,
                    "speaker": current_speaker,
                    "words": current_words,
                }
            )

        # Build full text
        full_text = " ".join(seg["text"] for seg in segments)

        return {
            "audio_file": original_path,
            "language": language,
            "text": full_text,
            "segments": segments,
            "processing_time": 0,  # Will be updated by caller
            "model_used": "google-cloud-speech-v2-chirp_3",
            "timestamp": time.time(),
            "diarization_enabled": self.enable_diarization,
            "speakers_detected": len(speakers_detected) if speakers_detected else None,
            "chunks_processed": len(chunk_transcripts),
        }

    def _empty_transcript(self, audio_path: str) -> Dict:
        """Return an empty transcript structure."""
        return {
            "audio_file": audio_path,
            "language": "en-US",
            "text": "",
            "segments": [],
            "processing_time": 0,
            "model_used": "google-cloud-speech-v2-chirp_3",
            "timestamp": time.time(),
            "diarization_enabled": self.enable_diarization,
            "speakers_detected": 0,
        }

    def _transcribe_batch(self, audio_path: str, language: str, podcast_title: Optional[str] = None) -> Any:
        """
        Transcribe using BatchRecognize (required for Chirp 3 with diarization).

        BatchRecognize is the only method that supports both Chirp 3 and diarization.
        Audio must be uploaded to GCS first.

        Args:
            audio_path: Path to audio file
            language: Language code
            podcast_title: Optional podcast title used as prefix for temp files in GCS
        """
        if not self.storage_client:
            raise RuntimeError(
                "Google Cloud Storage client not available. " "GCS is required for BatchRecognize with Chirp 3."
            )

        # Determine bucket name
        bucket_name = self.storage_bucket
        if not bucket_name:
            bucket_name = f"thestill-transcription-{self.project_id}"

        # Create blob name with timestamp in temp-audio/ folder
        # This folder has a lifecycle rule to auto-delete after 7 days
        sanitized_title = self._sanitize_filename(podcast_title) if podcast_title else ""
        prefix = f"{sanitized_title}_" if sanitized_title else ""

        blob_name = f"temp-audio/{prefix}{datetime.now().strftime('%Y%m%d-%H%M%S')}-" f"{Path(audio_path).name}"
        gcs_uri = f"gs://{bucket_name}/{blob_name}"

        try:
            # Get or create bucket
            bucket = self.storage_client.bucket(bucket_name)
            if not bucket.exists():
                print(f"Creating GCS bucket: {bucket_name}")
                bucket.create(location="US")
                # Add lifecycle rule to auto-delete temp-audio/ objects after 7 days
                # This prevents orphaned files if the app crashes before cleanup
                self._configure_bucket_lifecycle(bucket)

            # Upload audio file
            blob = bucket.blob(blob_name)
            print(f"Uploading to {gcs_uri}")
            blob.upload_from_filename(audio_path)

            # Build recognition config
            config = self._build_recognition_config(language)

            # Build request
            file_metadata = cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)

            request = cloud_speech.BatchRecognizeRequest(
                recognizer=f"projects/{self.project_id}/locations/{self.region}/recognizers/_",
                config=config,
                files=[file_metadata],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    inline_response_config=cloud_speech.InlineOutputConfig(),
                ),
            )

            # Start batch transcription
            print("Starting BatchRecognize transcription (this may take several minutes)...")
            operation = self.speech_client.batch_recognize(request=request)

            # Wait for completion with progress tracking
            last_progress = None
            op_start_time = time.time()

            while not operation.done():
                elapsed = time.time() - op_start_time
                metadata = operation.metadata

                # Try to extract progress from metadata
                progress = None
                if metadata and hasattr(metadata, "progress_percent"):
                    progress = metadata.progress_percent

                if progress is not None and progress != last_progress:
                    last_progress = progress
                    if progress > 0:
                        total_estimated = elapsed / (progress / 100.0)
                        remaining = total_estimated - elapsed
                        print(
                            f"Progress: {progress}% "
                            f"(elapsed: {self._format_time(elapsed)}, "
                            f"est. remaining: {self._format_time(remaining)})"
                        )
                    else:
                        print(f"Progress: 0% (elapsed: {self._format_time(elapsed)})")
                else:
                    # Print waiting message if no progress info (concise output)
                    print(f"Still waiting for BatchRecognize operation (elapsed: {self._format_time(elapsed)})...")

                time.sleep(15)

                # Timeout check
                if elapsed > 3600:  # 1 hour timeout
                    raise TimeoutError("Transcription timed out after 1 hour")

            response = operation.result()

            # Clean up temporary file
            blob.delete()
            print("Cleaned up temporary file from GCS")

            # Extract transcript from batch response
            # Response structure: BatchRecognizeResponse.results[uri] -> BatchRecognizeFileResult
            # BatchRecognizeFileResult.inline_result -> InlineResult
            # InlineResult.transcript -> BatchRecognizeResults
            # BatchRecognizeResults.results[] -> SpeechRecognitionResult[]
            if gcs_uri in response.results:
                file_result = response.results[gcs_uri]
            else:
                # Try to find result by iterating (in case URI format differs)
                for uri, file_result in response.results.items():
                    break
                else:
                    raise RuntimeError("No transcript found in batch response")

            # Check for errors
            if file_result.error and file_result.error.code != 0:
                raise RuntimeError(f"Transcription error: {file_result.error.message}")

            # Get transcript from inline_result (we used InlineOutputConfig)
            if file_result.inline_result and file_result.inline_result.transcript:
                return file_result.inline_result.transcript
            else:
                raise RuntimeError("No inline transcript found in response")

        except Exception as e:
            # Clean up on error
            try:
                blob.delete()
            except Exception:
                pass
            raise RuntimeError(f"BatchRecognize failed: {e}")

    def _build_recognition_config(self, language: str) -> cloud_speech.RecognitionConfig:
        """Build V2 recognition configuration with Chirp 3 and optional diarization."""
        # Build features configuration
        # Note: Chirp 3 does not support enable_word_confidence
        features_kwargs = {
            "enable_automatic_punctuation": True,
            "enable_word_time_offsets": True,
        }

        # Add diarization if enabled
        if self.enable_diarization:
            diarization_config = cloud_speech.SpeakerDiarizationConfig(
                min_speaker_count=self.min_speakers if self.min_speakers else 1,
                max_speaker_count=self.max_speakers if self.max_speakers else 6,
            )
            features_kwargs["diarization_config"] = diarization_config

            min_display = self.min_speakers if self.min_speakers else "1 (default)"
            max_display = self.max_speakers if self.max_speakers else "6 (default)"
            print(f"Diarization enabled (min={min_display}, max={max_display})")

        features = cloud_speech.RecognitionFeatures(**features_kwargs)

        # Build main config
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[language],
            model="chirp_3",
            features=features,
        )

        return config

    def _format_time(self, seconds: float) -> str:
        """
        Format seconds into human-readable time string.

        Examples:
            45 seconds → "45s"
            125 seconds → "2m 5s"
            3725 seconds → "1h 2m 5s"
        """
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            return f"{hours}h {mins}m {secs}s"

    def _format_transcript(self, transcript: Any, processing_time: float, audio_path: str) -> Dict:
        """
        Format Google Cloud V2 response to match Whisper transcript structure.

        Output format:
        {
            "audio_file": str,
            "language": str,
            "text": str,  # Full transcript text
            "segments": [
                {
                    "id": int,
                    "start": float,
                    "end": float,
                    "text": str,
                    "speaker": str,  # "SPEAKER_01", "SPEAKER_02", etc.
                    "words": [
                        {
                            "word": str,
                            "start": float,
                            "end": float,
                            "probability": float,
                            "speaker": str
                        }
                    ]
                }
            ],
            "processing_time": float,
            "model_used": str,
            "timestamp": float,
            "diarization_enabled": bool,
            "speakers_detected": int
        }
        """
        segments = []
        full_text_parts = []
        speakers_detected = set()

        segment_id = 0
        detected_language = "en-US"

        for result in transcript.results:
            if not result.alternatives:
                continue

            alternative = result.alternatives[0]

            # Get detected language if available
            if hasattr(result, "language_code") and result.language_code:
                detected_language = result.language_code

            # Check if we have word-level information
            if hasattr(alternative, "words") and alternative.words:
                if self.enable_diarization:
                    # Group words by speaker to create segments
                    current_speaker = None
                    current_words = []
                    current_start = 0.0
                    current_end = 0.0

                    for word_info in alternative.words:
                        # V2 API uses different time format
                        word_start = self._get_seconds(word_info.start_offset)
                        word_end = self._get_seconds(word_info.end_offset)

                        # Get speaker tag (V2 uses speaker_label)
                        speaker_label = getattr(word_info, "speaker_label", None)
                        if speaker_label:
                            speaker_id = f"SPEAKER_{speaker_label}"
                        else:
                            speaker_id = "SPEAKER_01"
                        speakers_detected.add(speaker_id)

                        # Start new segment if speaker changed
                        if current_speaker != speaker_id:
                            # Save previous segment
                            if current_words:
                                segment_text = " ".join(w["word"] for w in current_words)
                                segments.append(
                                    {
                                        "id": segment_id,
                                        "start": current_start,
                                        "end": current_end,
                                        "text": segment_text,
                                        "speaker": current_speaker,
                                        "words": current_words,
                                    }
                                )
                                full_text_parts.append(segment_text)
                                segment_id += 1

                            # Start new segment
                            current_speaker = speaker_id
                            current_words = []
                            current_start = word_start

                        # Add word to current segment
                        confidence = getattr(word_info, "confidence", 0.0)
                        current_words.append(
                            {
                                "word": word_info.word,
                                "start": word_start,
                                "end": word_end,
                                "probability": confidence,
                                "speaker": speaker_id,
                            }
                        )
                        current_end = word_end

                    # Save final segment
                    if current_words:
                        segment_text = " ".join(w["word"] for w in current_words)
                        segments.append(
                            {
                                "id": segment_id,
                                "start": current_start,
                                "end": current_end,
                                "text": segment_text,
                                "speaker": current_speaker,
                                "words": current_words,
                            }
                        )
                        full_text_parts.append(segment_text)
                        segment_id += 1
                else:
                    # No diarization - create segment from words
                    words = []
                    for word_info in alternative.words:
                        word_start = self._get_seconds(word_info.start_offset)
                        word_end = self._get_seconds(word_info.end_offset)
                        confidence = getattr(word_info, "confidence", 0.0)
                        words.append(
                            {
                                "word": word_info.word,
                                "start": word_start,
                                "end": word_end,
                                "probability": confidence,
                                "speaker": None,
                            }
                        )

                    if words:
                        segment_text = " ".join(w["word"] for w in words)
                        segments.append(
                            {
                                "id": segment_id,
                                "start": words[0]["start"],
                                "end": words[-1]["end"],
                                "text": segment_text,
                                "speaker": None,
                                "words": words,
                            }
                        )
                        full_text_parts.append(segment_text)
                        segment_id += 1

            elif hasattr(alternative, "transcript") and alternative.transcript:
                # Fallback: use transcript text without word-level info
                segment_text = alternative.transcript.strip()
                if segment_text:
                    segments.append(
                        {
                            "id": segment_id,
                            "start": 0.0,
                            "end": 0.0,
                            "text": segment_text,
                            "speaker": None,
                            "words": [],
                        }
                    )
                    full_text_parts.append(segment_text)
                    segment_id += 1

        full_text = " ".join(full_text_parts)

        return {
            "audio_file": audio_path,
            "language": detected_language,
            "text": full_text,
            "segments": segments,
            "processing_time": processing_time,
            "model_used": "google-cloud-speech-v2-chirp_3",
            "timestamp": time.time(),
            "diarization_enabled": self.enable_diarization,
            "speakers_detected": len(speakers_detected) if self.enable_diarization else None,
        }

    def _get_seconds(self, duration) -> float:
        """Convert protobuf Duration to seconds."""
        if duration is None:
            return 0.0
        if hasattr(duration, "total_seconds"):
            return duration.total_seconds()
        # Handle protobuf Duration type
        if hasattr(duration, "seconds") and hasattr(duration, "nanos"):
            return duration.seconds + duration.nanos / 1e9
        return 0.0

    def _save_transcript(self, transcript_data: Dict, output_path: str):
        """Save transcript to JSON file."""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)
            print(f"Transcript saved to: {output_path}")
        except Exception as e:
            print(f"Error saving transcript: {e}")

    def get_transcript_text(self, transcript_data: Dict) -> str:
        """Extract plain text from transcript data with speaker labels and timestamps."""
        if not transcript_data or "segments" not in transcript_data:
            return ""

        text_parts = []
        for segment in transcript_data["segments"]:
            text = segment.get("text", "").strip()
            if text:
                start_time = segment.get("start", 0)
                minutes = int(start_time // 60)
                seconds = int(start_time % 60)
                timestamp = f"[{minutes:02d}:{seconds:02d}]"

                # Add speaker label if available
                speaker = segment.get("speaker")
                if speaker:
                    text_parts.append(f"{timestamp} [{speaker}] {text}")
                else:
                    text_parts.append(f"{timestamp} {text}")

        return "\n".join(text_parts)
