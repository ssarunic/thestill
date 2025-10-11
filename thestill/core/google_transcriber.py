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
Google Cloud Speech-to-Text transcriber with speaker diarization.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from pydub import AudioSegment

try:
    from google.cloud import speech
    from google.cloud import storage
    from google.oauth2 import service_account
    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    GOOGLE_CLOUD_AVAILABLE = False
    # Create dummy module references for type hints when library not available
    speech = None  # type: ignore
    storage = None  # type: ignore
    service_account = None  # type: ignore


class GoogleCloudTranscriber:
    """
    Google Cloud Speech-to-Text transcriber with automatic diarization support.

    Features:
    - Synchronous transcription for files < 10MB
    - Asynchronous transcription via GCS for larger files
    - Built-in speaker diarization
    - Word-level timestamps
    - Automatic punctuation

    Output format matches WhisperTranscriber for compatibility with existing pipeline.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        storage_bucket: Optional[str] = None,
        enable_diarization: bool = True,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None
    ):
        """
        Initialize Google Cloud Speech transcriber.

        Args:
            credentials_path: Path to service account JSON key file
            project_id: Google Cloud project ID
            storage_bucket: GCS bucket name for large files (optional, will auto-create)
            enable_diarization: Enable speaker diarization
            min_speakers: Minimum number of speakers (None = auto-detect)
            max_speakers: Maximum number of speakers (None = auto-detect)
        """
        if not GOOGLE_CLOUD_AVAILABLE:
            raise ImportError(
                "Google Cloud Speech-to-Text libraries not installed"
            )

        self.credentials_path = credentials_path
        self.project_id = project_id
        self.storage_bucket = storage_bucket
        self.enable_diarization = enable_diarization
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers

        self.speech_client = None
        self.storage_client = None
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize Google Cloud clients."""
        try:
            if self.credentials_path:
                credentials = service_account.Credentials.from_service_account_file(
                    self.credentials_path
                )
                self.speech_client = speech.SpeechClient(credentials=credentials)
                self.storage_client = storage.Client(
                    credentials=credentials,
                    project=self.project_id
                )
            else:
                # Use default credentials (e.g., from GOOGLE_APPLICATION_CREDENTIALS env var)
                self.speech_client = speech.SpeechClient()
                try:
                    self.storage_client = storage.Client(project=self.project_id)
                except Exception:
                    print("WARNING: Google Cloud Storage client not available - large file support limited")

            print("Google Cloud Speech client initialized")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Google Cloud clients: {e}")

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: str = None,
        language: str = "en-US",
        custom_prompt: str = None,
        preprocess_audio: bool = False,
        clean_transcript: bool = False,
        cleaning_config: Dict = None
    ) -> Optional[Dict]:
        """
        Transcribe audio file with optional speaker diarization.

        Args:
            audio_path: Path to audio file (must be downsampled 16kHz WAV)
            output_path: Path to save transcript JSON
            language: Language code (e.g., 'en-US', 'hr-HR')
            custom_prompt: Not used for Google Cloud (included for API compatibility)
            preprocess_audio: Not used for Google Cloud (included for API compatibility)
            clean_transcript: Not used for Google Cloud (included for API compatibility)
            cleaning_config: Not used for Google Cloud (included for API compatibility)

        Returns:
            Transcript dictionary matching Whisper format, or None on error
        """
        try:
            print(f"Starting Google Cloud transcription of: {Path(audio_path).name}")
            start_time = time.time()

            # Get file size to determine transcription method
            file_size = os.path.getsize(audio_path)
            max_sync_size = 10 * 1024 * 1024  # 10MB

            if file_size <= max_sync_size:
                print(f"Using synchronous transcription (file size: {file_size / 1024 / 1024:.1f}MB)")
                result = self._transcribe_sync(audio_path, language)
            else:
                print(f"Using asynchronous transcription via GCS (file size: {file_size / 1024 / 1024:.1f}MB)")
                result = self._transcribe_async_gcs(audio_path, language)

            processing_time = time.time() - start_time
            print(f"Transcription completed in {processing_time:.1f} seconds")

            # Format result to match Whisper output structure
            transcript_data = self._format_transcript(result, processing_time, audio_path)

            if output_path:
                self._save_transcript(transcript_data, output_path)

            return transcript_data

        except Exception as e:
            print(f"Error transcribing {audio_path}: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _transcribe_sync(self, audio_path: str, language: str) -> Any:
        """Synchronous transcription for files < 10MB."""
        with open(audio_path, 'rb') as audio_file:
            audio_content = audio_file.read()

        audio = speech.RecognitionAudio(content=audio_content)
        config = self._build_recognition_config(language)

        print("Starting synchronous transcription...")
        response = self.speech_client.recognize(config=config, audio=audio)

        return response

    def _transcribe_async_gcs(self, audio_path: str, language: str) -> Any:
        """Asynchronous transcription via Google Cloud Storage for large files."""
        if not self.storage_client:
            raise RuntimeError(
                "Google Cloud Storage client not available. "
                "Cannot transcribe files larger than 10MB without GCS."
            )

        # Determine bucket name
        bucket_name = self.storage_bucket
        if not bucket_name:
            if not self.project_id:
                raise ValueError("GOOGLE_CLOUD_PROJECT_ID required for GCS transcription")
            bucket_name = f"thestill-transcription-{self.project_id}"

        # Create blob name with timestamp
        from datetime import datetime
        blob_name = f"temp-audio-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{Path(audio_path).name}"

        try:
            # Get or create bucket
            bucket = self.storage_client.bucket(bucket_name)
            if not bucket.exists():
                print(f"Creating GCS bucket: {bucket_name}")
                bucket.create(location="US")

            # Upload audio file
            blob = bucket.blob(blob_name)
            print(f"Uploading to gs://{bucket_name}/{blob_name}")
            blob.upload_from_filename(audio_path)

            # Configure recognition
            audio = speech.RecognitionAudio(uri=f"gs://{bucket_name}/{blob_name}")
            config = self._build_recognition_config(language)

            # Start long-running operation
            print("Starting long-running transcription (this may take several minutes)...")
            operation = self.speech_client.long_running_recognize(config=config, audio=audio)

            # Wait for completion with progress updates
            elapsed = 0
            while not operation.done():
                time.sleep(30)
                elapsed += 30
                print(f"Transcription in progress... ({elapsed}s elapsed)")

                if elapsed > 3600:  # 1 hour timeout
                    raise TimeoutError("Transcription timed out after 1 hour")

            response = operation.result()

            # Clean up temporary file
            blob.delete()
            print("Cleaned up temporary file from GCS")

            return response

        except Exception as e:
            # Clean up on error
            try:
                blob.delete()
            except:
                pass
            raise RuntimeError(f"GCS transcription failed: {e}")

    def _build_recognition_config(self, language: str) -> Any:
        """Build recognition configuration with diarization if enabled."""
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=language,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
            model='latest_long'
        )

        # Add diarization configuration if enabled
        if self.enable_diarization:
            diarization_config = speech.SpeakerDiarizationConfig(
                enable_speaker_diarization=True
            )

            # Only set speaker counts if explicitly provided
            if self.min_speakers is not None:
                diarization_config.min_speaker_count = self.min_speakers
            if self.max_speakers is not None:
                diarization_config.max_speaker_count = self.max_speakers

            config.diarization_config = diarization_config

            min_display = self.min_speakers if self.min_speakers is not None else 'Google default'
            max_display = self.max_speakers if self.max_speakers is not None else 'Google default'
            print(f"Diarization enabled (min={min_display}, max={max_display})")

        return config

    def _format_transcript(
        self,
        response: Any,
        processing_time: float,
        audio_path: str
    ) -> Dict:
        """
        Format Google Cloud response to match Whisper transcript structure.

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

        for result in response.results:
            alternative = result.alternatives[0]

            # Check if we have word-level diarization
            if hasattr(alternative, 'words') and alternative.words and self.enable_diarization:
                # Group words by speaker to create segments
                current_speaker = None
                current_words = []
                current_start = 0.0
                current_end = 0.0

                for word_info in alternative.words:
                    word_start = word_info.start_time.total_seconds() if word_info.start_time else 0.0
                    word_end = word_info.end_time.total_seconds() if word_info.end_time else 0.0
                    speaker_tag = getattr(word_info, 'speaker_tag', 1)
                    speaker_id = f"SPEAKER_{speaker_tag:02d}"
                    speakers_detected.add(speaker_id)

                    # Start new segment if speaker changed
                    if current_speaker != speaker_id:
                        # Save previous segment
                        if current_words:
                            segment_text = ' '.join(w['word'] for w in current_words)
                            segments.append({
                                "id": segment_id,
                                "start": current_start,
                                "end": current_end,
                                "text": segment_text,
                                "speaker": current_speaker,
                                "words": current_words
                            })
                            full_text_parts.append(segment_text)
                            segment_id += 1

                        # Start new segment
                        current_speaker = speaker_id
                        current_words = []
                        current_start = word_start

                    # Add word to current segment
                    current_words.append({
                        "word": word_info.word,
                        "start": word_start,
                        "end": word_end,
                        "probability": getattr(word_info, 'confidence', alternative.confidence),
                        "speaker": speaker_id
                    })
                    current_end = word_end

                # Save final segment
                if current_words:
                    segment_text = ' '.join(w['word'] for w in current_words)
                    segments.append({
                        "id": segment_id,
                        "start": current_start,
                        "end": current_end,
                        "text": segment_text,
                        "speaker": current_speaker,
                        "words": current_words
                    })
                    full_text_parts.append(segment_text)
                    segment_id += 1

            else:
                # No diarization - create single segment per result
                segment_text = alternative.transcript.strip()
                if segment_text:
                    # Extract words if available
                    words = []
                    if hasattr(alternative, 'words') and alternative.words:
                        for word_info in alternative.words:
                            words.append({
                                "word": word_info.word,
                                "start": word_info.start_time.total_seconds() if word_info.start_time else 0.0,
                                "end": word_info.end_time.total_seconds() if word_info.end_time else 0.0,
                                "probability": getattr(word_info, 'confidence', alternative.confidence),
                                "speaker": None
                            })

                    start_time = words[0]['start'] if words else 0.0
                    end_time = words[-1]['end'] if words else 0.0

                    segments.append({
                        "id": segment_id,
                        "start": start_time,
                        "end": end_time,
                        "text": segment_text,
                        "speaker": None,
                        "words": words
                    })
                    full_text_parts.append(segment_text)
                    segment_id += 1

        full_text = " ".join(full_text_parts)

        return {
            "audio_file": audio_path,
            "language": response.results[0].language_code if response.results else "en-US",
            "text": full_text,
            "segments": segments,
            "processing_time": processing_time,
            "model_used": "google-cloud-speech",
            "timestamp": time.time(),
            "diarization_enabled": self.enable_diarization,
            "speakers_detected": len(speakers_detected) if self.enable_diarization else None
        }

    def _save_transcript(self, transcript_data: Dict, output_path: str):
        """Save transcript to JSON file."""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
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
