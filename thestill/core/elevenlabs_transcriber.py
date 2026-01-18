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
ElevenLabs Speech-to-Text transcriber using Scribe v1 model.

Features:
- High accuracy transcription with Scribe v1 model
- Speaker diarization (up to 32 speakers)
- Word-level timestamps
- Language detection
- Audio event detection (optional)
"""

import logging
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
from tenacity import retry, stop_after_attempt, wait_exponential

from thestill.models.transcript import Segment, Transcript, Word
from thestill.utils.path_manager import PathManager

from .progress import ProgressCallback, ProgressUpdate, TranscriptionStage
from .transcriber import Transcriber

logger = logging.getLogger(__name__)

# ElevenLabs API configuration
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_TRANSCRIPT_URL = "https://api.elevenlabs.io/v1/speech-to-text/transcripts"
DEFAULT_MODEL = "scribe_v1"

# Timeout configuration
UPLOAD_TIMEOUT = 300  # 5 minutes for file upload (sync mode fallback)
SYNC_REQUEST_TIMEOUT = 1800  # 30 minutes for sync mode (small files)
POLL_TIMEOUT = 30  # 30 seconds for polling requests
POLL_INTERVAL = 10  # seconds between polls
MAX_POLL_DURATION = 7200  # 2 hours max wait for transcript (supports ~4hr podcasts)

# File size threshold for async mode (bytes)
# Files larger than this use async mode with polling
ASYNC_THRESHOLD_MB = 50  # Use async for files > 50MB

# Upload progress reporting interval (percentage points)
# Log progress every N percent to avoid spamming logs
UPLOAD_PROGRESS_LOG_INTERVAL = 10  # Log every 10%


class ElevenLabsTranscriber(Transcriber):
    """
    ElevenLabs Speech-to-Text transcriber using Scribe v1 model.

    Features:
    - Uses Scribe v1 model for high accuracy transcription
    - Built-in speaker diarization (up to 32 speakers)
    - Word-level timestamps
    - Language detection and specification
    - Optional audio event detection (laughter, applause, etc.)

    Output format matches other transcribers for compatibility with existing pipeline.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        enable_diarization: bool = True,
        num_speakers: Optional[int] = None,
        language: Optional[str] = None,
        tag_audio_events: bool = False,
        path_manager: Optional[PathManager] = None,
        use_async: bool = True,
        async_threshold_mb: int = 0,
        wait_for_webhook: bool = False,
    ):
        """
        Initialize ElevenLabs Speech-to-Text transcriber.

        Args:
            api_key: ElevenLabs API key. If not provided, reads from ELEVENLABS_API_KEY env var.
            model: Model to use. Options: "scribe_v1", "scribe_v1_experimental"
            enable_diarization: Enable speaker diarization (default: True)
            num_speakers: Expected number of speakers (None = auto-detect)
            language: Language code (e.g., "en"). None = auto-detect
            tag_audio_events: Enable detection of audio events like laughter, applause
            path_manager: PathManager for storing pending operations (required for async mode)
            use_async: Use async mode with polling (default: True). Falls back to sync for small files.
            async_threshold_mb: File size threshold for async mode (0 = always async when use_async=True)
            wait_for_webhook: If True, don't poll after submission - wait for webhook callback instead.
                              The transcript will be saved by the webhook handler. Returns None immediately.
        """
        import os

        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "ElevenLabs API key is required. "
                "Set ELEVENLABS_API_KEY environment variable or pass api_key parameter."
            )

        self.model = model
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self.language = language
        self.tag_audio_events = tag_audio_events
        self.path_manager = path_manager
        self.use_async = use_async
        self.async_threshold_mb = async_threshold_mb
        self.wait_for_webhook = wait_for_webhook

        logger.info(f"Initialized ElevenLabs transcriber with model: {self.model}")
        logger.info(f"Async mode: {'enabled' if self.use_async else 'disabled'}")
        if self.use_async:
            if self.async_threshold_mb > 0:
                logger.info(f"Async threshold: {self.async_threshold_mb} MB")
            else:
                logger.info("Async threshold: 0 MB (always use async)")
            if self.wait_for_webhook:
                logger.info("Webhook mode: enabled (will not poll, waiting for webhook callback)")
        if self.enable_diarization:
            speakers_info = f"num_speakers={self.num_speakers}" if self.num_speakers else "auto-detect"
            logger.info(f"Diarization enabled ({speakers_info})")

    def load_model(self) -> None:
        """No-op for cloud-based transcriber. Model is loaded server-side."""
        pass

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        language: str,
        custom_prompt: Optional[str] = None,
        preprocess_audio: bool = False,
        clean_transcript: bool = False,
        cleaning_config: Optional[Dict[str, Any]] = None,
        podcast_title: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Optional[Transcript]:
        """
        Transcribe audio file using ElevenLabs Speech-to-Text API.

        For large files (>50MB by default), uses async mode:
        1. Submit transcription with webhook=true
        2. Get transcription_id immediately
        3. Poll GET /transcripts/{id} until ready
        4. Save pending operation to disk for resume capability

        Args:
            audio_path: Path to audio file
            output_path: Optional path to save transcript JSON
            language: Language code (ISO 639-1, e.g., 'en', 'hr'). Overrides instance setting.
            custom_prompt: Not supported by ElevenLabs (ignored)
            preprocess_audio: Not used (ignored for API compatibility)
            clean_transcript: Not used (ignored for API compatibility)
            cleaning_config: Not used (ignored for API compatibility)
            podcast_title: Not used (ignored for API compatibility)
            episode_id: Episode UUID for operation persistence
            podcast_slug: Podcast slug for operation persistence
            episode_slug: Episode slug for operation persistence
            progress_callback: Optional callback for upload and transcription progress updates

        Returns:
            Transcript object with segments and metadata. None on error.
        """
        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"Audio file not found: {audio_path}")
            return None

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(f"Transcribing {audio_file.name} ({file_size_mb:.1f} MB)")

        if custom_prompt:
            logger.warning("ElevenLabs does not support custom prompts. Ignoring custom_prompt parameter.")

        # Decide sync vs async based on file size and settings
        # Use instance threshold (0 = always async when use_async=True)
        threshold = self.async_threshold_mb if self.async_threshold_mb > 0 else 0
        use_async_mode = self.use_async and file_size_mb > threshold

        if use_async_mode:
            logger.info(f"Using async mode (file > {threshold}MB threshold)")
            return self._transcribe_async(
                audio_path=audio_path,
                output_path=output_path,
                language=language,
                episode_id=episode_id,
                podcast_slug=podcast_slug,
                episode_slug=episode_slug,
                progress_callback=progress_callback,
            )
        else:
            logger.info("Using sync mode")
            return self._transcribe_sync(
                audio_path=audio_path,
                output_path=output_path,
                language=language,
                progress_callback=progress_callback,
            )

    def _transcribe_sync(
        self,
        audio_path: str,
        output_path: Optional[str],
        language: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Optional[Transcript]:
        """
        Synchronous transcription - wait for response in single request.

        Used for smaller files where the API can respond within timeout.
        """
        start_time = time.time()

        try:
            response_data = self._call_api_sync(audio_path, language, progress_callback)
            transcript = self._format_response(response_data, audio_path, start_time)

            if output_path:
                self._save_transcript(transcript, output_path)

            return transcript

        except requests.exceptions.HTTPError as e:
            logger.error(f"ElevenLabs API error: {e}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_detail = e.response.json()
                    logger.error(f"API error details: {error_detail}")
                except Exception:
                    logger.error(f"Response text: {e.response.text}")
            return None
        except Exception as e:
            self._log_error(e)
            return None

    def _transcribe_async(
        self,
        audio_path: str,
        output_path: Optional[str],
        language: str,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Optional[Transcript]:
        """
        Asynchronous transcription - submit job and poll for results.

        Flow:
        1. Submit with webhook=true to get transcription_id immediately
        2. Save pending operation to disk (for resume if app crashes)
        3. Poll GET /transcripts/{id} until ready
        4. Clean up pending operation file on success

        Note: If webhooks are not configured in ElevenLabs account,
        automatically falls back to sync mode.
        """
        start_time = time.time()

        # Build webhook metadata for correlation
        # This is included in the webhook callback to identify which episode the transcript belongs to
        webhook_metadata: Optional[Dict[str, Any]] = None
        if episode_id:
            webhook_metadata = {
                "episode_id": episode_id,
                "podcast_slug": podcast_slug,
                "episode_slug": episode_slug,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }

        try:
            # Step 1: Submit transcription request (async mode)
            try:
                submit_response = self._submit_async_transcription(
                    audio_path, language, webhook_metadata, progress_callback
                )
            except requests.exceptions.HTTPError as e:
                # Check for "no webhooks configured" error - fallback to sync
                if e.response is not None and e.response.status_code == 400:
                    try:
                        error_data = e.response.json()
                        if error_data.get("detail", {}).get("status") == "no_webhooks_configured":
                            logger.warning(
                                "Webhooks not configured in ElevenLabs account. "
                                "Falling back to sync mode (may timeout for large files). "
                                "Configure webhooks at https://elevenlabs.io/app/speech-to-text/webhooks"
                            )
                            return self._transcribe_sync(audio_path, output_path, language, progress_callback)
                    except (ValueError, KeyError):
                        pass
                raise  # Re-raise if not the specific error we're handling

            transcription_id = submit_response.get("transcription_id")
            if not transcription_id:
                logger.error("No transcription_id in async response. Falling back to sync mode.")
                return self._transcribe_sync(audio_path, output_path, language, progress_callback)

            logger.info(f"Transcription submitted. ID: {transcription_id}")

            # Step 2: Save pending operation for resume capability
            if self.path_manager and episode_id:
                self._save_pending_operation(
                    transcription_id=transcription_id,
                    audio_path=audio_path,
                    language=language,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                )

            # Step 3: If waiting for webhook, register with tracker and return None
            # The webhook handler will save the transcript when the callback arrives
            if self.wait_for_webhook:
                # Register this episode as pending so CLI can track completion
                # Note: We use episode_id (not transcription_id) because ElevenLabs returns
                # a different request_id in the webhook callback than the transcription_id
                # from the submission response. episode_id is consistent in both.
                from thestill.webhook import get_tracker

                tracker = get_tracker()
                if episode_id:
                    tracker.add_pending(episode_id)
                    logger.info(
                        f"Webhook mode: Transcription {transcription_id} submitted for episode {episode_id}. "
                        "Waiting for webhook callback to deliver results."
                    )
                else:
                    logger.warning(
                        f"Webhook mode: Transcription {transcription_id} submitted but no episode_id provided. "
                        "Cannot track completion - webhook callback may not trigger auto-exit."
                    )
                return None

            # Step 3 (polling mode): Poll for completion
            response_data = self._poll_for_transcript(transcription_id)

            if response_data is None:
                logger.error("Transcription polling failed or timed out")
                return None

            # Step 4: Format response and clean up
            transcript = self._format_response(response_data, audio_path, start_time)

            if self.path_manager:
                self._remove_pending_operation(transcription_id)

            if output_path:
                self._save_transcript(transcript, output_path)

            return transcript

        except Exception as e:
            self._log_error(e)
            return None

    def _log_error(self, e: Exception) -> None:
        """Log error with unwrapped cause details."""
        error_msg = str(e)
        if hasattr(e, "__cause__") and e.__cause__ is not None:
            cause = e.__cause__
            error_msg = f"{error_msg} - Cause: {cause}"
            if isinstance(cause, requests.exceptions.HTTPError):
                if hasattr(cause, "response") and cause.response is not None:
                    try:
                        error_detail = cause.response.json()
                        error_msg = f"{error_msg} - Details: {error_detail}"
                    except Exception:
                        error_msg = f"{error_msg} - Response: {cause.response.text[:500]}"
        logger.error(f"Transcription error: {error_msg}")

    def _build_request_data(
        self,
        language: str,
        async_mode: bool = False,
        webhook_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build common request data for API calls."""
        data = {
            "model_id": self.model,
            "timestamps_granularity": "word",
            "diarize": str(self.enable_diarization).lower(),
            "tag_audio_events": str(self.tag_audio_events).lower(),
        }

        # Use instance language setting or parameter
        effective_language = self.language or language
        if effective_language:
            data["language_code"] = effective_language

        # Add num_speakers if specified
        if self.num_speakers:
            data["num_speakers"] = str(self.num_speakers)

        # Enable async mode (webhook=true returns transcription_id immediately)
        if async_mode:
            data["webhook"] = "true"

            # Include webhook_metadata for correlation when webhook callback is received
            # This allows the webhook handler to identify which episode the transcript belongs to
            if webhook_metadata:
                import json

                data["webhook_metadata"] = json.dumps(webhook_metadata)

        return data

    def _create_upload_monitor(
        self,
        audio_path: str,
        data: Dict[str, Any],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[MultipartEncoderMonitor, Dict[str, str]]:
        """
        Create a MultipartEncoderMonitor for tracking upload progress.

        Args:
            audio_path: Path to audio file
            data: Form data dict to include in the request
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (monitor, headers) ready for requests.post()
        """
        audio_file = Path(audio_path)
        file_size = audio_file.stat().st_size
        file_size_mb = file_size / (1024 * 1024)

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(audio_path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        # Track last logged percentage to avoid log spam
        last_logged_pct = -UPLOAD_PROGRESS_LOG_INTERVAL  # Ensure first log at 0%
        upload_complete_signaled = False  # Track if we've signaled upload completion

        def progress_monitor_callback(monitor: MultipartEncoderMonitor) -> None:
            nonlocal last_logged_pct, upload_complete_signaled

            bytes_read = monitor.bytes_read
            pct = int((bytes_read / file_size) * 100) if file_size > 0 else 100
            mb_uploaded = bytes_read / (1024 * 1024)

            # Log progress at intervals
            if pct >= last_logged_pct + UPLOAD_PROGRESS_LOG_INTERVAL or pct == 100:
                last_logged_pct = (pct // UPLOAD_PROGRESS_LOG_INTERVAL) * UPLOAD_PROGRESS_LOG_INTERVAL
                logger.info(f"Upload progress: {pct}% ({mb_uploaded:.1f} MB / {file_size_mb:.1f} MB)")

            # Call progress callback for web UI
            if progress_callback:
                if pct < 100:
                    # Still uploading
                    progress_callback(
                        ProgressUpdate(
                            stage=TranscriptionStage.UPLOADING,
                            progress_pct=pct,
                            message=f"Uploading: {pct}% ({mb_uploaded:.1f} MB / {file_size_mb:.1f} MB)",
                        )
                    )
                elif not upload_complete_signaled:
                    # Upload complete, now waiting for transcription
                    upload_complete_signaled = True
                    progress_callback(
                        ProgressUpdate(
                            stage=TranscriptionStage.TRANSCRIBING,
                            progress_pct=0,
                            message="Waiting for ElevenLabs to transcribe...",
                        )
                    )

        # Build multipart encoder with file and form data
        # Note: MultipartEncoder requires file to be opened in binary mode
        fields = {
            "file": (audio_file.name, open(audio_path, "rb"), mime_type),
        }
        # Add all form data fields
        for key, value in data.items():
            fields[key] = value

        encoder = MultipartEncoder(fields=fields)
        monitor = MultipartEncoderMonitor(encoder, progress_monitor_callback)

        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": monitor.content_type,
        }

        return monitor, headers

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    def _call_api_sync(
        self,
        audio_path: str,
        language: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """
        Call ElevenLabs Speech-to-Text API synchronously with retry logic.

        Args:
            audio_path: Path to audio file
            language: Language code
            progress_callback: Optional callback for upload progress updates

        Returns:
            API response as dictionary with transcript data

        Raises:
            requests.exceptions.HTTPError: On API errors
        """
        data = self._build_request_data(language, async_mode=False)

        logger.info(f"Calling ElevenLabs API (sync, timeout={SYNC_REQUEST_TIMEOUT}s)...")
        logger.debug(f"Request data: {data}")

        # Create upload monitor with progress tracking
        monitor, headers = self._create_upload_monitor(audio_path, data, progress_callback)

        try:
            response = requests.post(
                ELEVENLABS_API_URL,
                headers=headers,
                data=monitor,
                timeout=SYNC_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error(f"Request timed out after {SYNC_REQUEST_TIMEOUT}s. Consider using async mode.")
            raise
        except requests.exceptions.HTTPError as e:
            self._log_http_error(e)
            raise

        logger.info("ElevenLabs API call successful")
        return response.json()

    def _submit_async_transcription(
        self,
        audio_path: str,
        language: str,
        webhook_metadata: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """
        Submit transcription request in async mode.

        With webhook=true, the API returns immediately with a transcription_id
        instead of waiting for the transcription to complete.

        Note: No retry decorator - we want to fail fast on webhook config errors
        so we can fallback to sync mode immediately.

        Args:
            audio_path: Path to audio file
            language: Language code
            webhook_metadata: Optional metadata to include in webhook callback
            progress_callback: Optional callback for upload progress updates

        Returns:
            API response with transcription_id, message, request_id

        Raises:
            requests.exceptions.HTTPError: On API errors (including webhook not configured)
        """
        data = self._build_request_data(language, async_mode=True, webhook_metadata=webhook_metadata)

        logger.info(f"Submitting async transcription (upload timeout={UPLOAD_TIMEOUT}s)...")
        logger.debug(f"Request data: {data}")

        # Create upload monitor with progress tracking
        monitor, headers = self._create_upload_monitor(audio_path, data, progress_callback)

        try:
            response = requests.post(
                ELEVENLABS_API_URL,
                headers=headers,
                data=monitor,
                timeout=UPLOAD_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error(f"Upload timed out after {UPLOAD_TIMEOUT}s.")
            raise
        except requests.exceptions.HTTPError as e:
            self._log_http_error(e)
            raise

        result = response.json()
        logger.info(f"Async submission successful: {result.get('message', 'OK')}")
        return result

    def _poll_for_transcript(self, transcription_id: str) -> Optional[Dict[str, Any]]:
        """
        Poll for transcript completion.

        Args:
            transcription_id: The transcription ID to poll

        Returns:
            Transcript data when ready, None if timeout or error
        """
        headers = {"xi-api-key": self.api_key}
        url = f"{ELEVENLABS_TRANSCRIPT_URL}/{transcription_id}"

        start_time = time.time()
        poll_count = 0

        logger.info(f"Polling for transcript (max {MAX_POLL_DURATION}s)...")

        while (time.time() - start_time) < MAX_POLL_DURATION:
            poll_count += 1
            elapsed = int(time.time() - start_time)

            try:
                response = requests.get(url, headers=headers, timeout=POLL_TIMEOUT)

                if response.status_code == 200:
                    result = response.json()
                    # Check if we got actual transcript data (has 'text' field)
                    if "text" in result:
                        logger.info(f"Transcript ready after {elapsed}s ({poll_count} polls)")
                        return result
                    else:
                        # Got a response but no transcript yet
                        logger.debug(f"Poll {poll_count}: No transcript data yet")

                elif response.status_code == 404:
                    # Transcript not ready yet - keep polling
                    logger.debug(f"Poll {poll_count} ({elapsed}s): Transcript not ready (404)")

                elif response.status_code == 202:
                    # Processing - keep polling
                    logger.debug(f"Poll {poll_count} ({elapsed}s): Still processing (202)")

                else:
                    # Unexpected status
                    logger.warning(f"Poll {poll_count}: Unexpected status {response.status_code}")

            except requests.exceptions.Timeout:
                logger.debug(f"Poll {poll_count}: Request timed out, retrying...")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Poll {poll_count}: Request error: {e}")

            # Log progress every 30 seconds
            if poll_count % 3 == 0:
                remaining = MAX_POLL_DURATION - elapsed
                logger.info(f"Still waiting for transcript... ({elapsed}s elapsed, ~{remaining}s remaining)")

            time.sleep(POLL_INTERVAL)

        logger.error(f"Polling timed out after {MAX_POLL_DURATION}s ({poll_count} polls)")
        return None

    def _log_http_error(self, e: requests.exceptions.HTTPError) -> None:
        """Log HTTP error with details."""
        status_code = e.response.status_code if e.response is not None else "unknown"
        logger.error(f"HTTP error {status_code}: {e}")
        if e.response is not None:
            try:
                error_body = e.response.json()
                logger.error(f"Error details: {error_body}")
            except Exception:
                logger.error(f"Response body: {e.response.text[:1000]}")

    def _format_response(
        self,
        response_data: Dict[str, Any],
        audio_path: str,
        start_time: float,
    ) -> Transcript:
        """
        Convert ElevenLabs API response to Transcript model.

        Args:
            response_data: Raw API response
            audio_path: Original audio file path
            start_time: Transcription start time for processing_time calculation

        Returns:
            Formatted Transcript object
        """
        processing_time = time.time() - start_time

        # Extract language
        language = response_data.get("language_code", "en")

        # Extract full text
        full_text = response_data.get("text", "")

        # Build words list from response
        raw_words = response_data.get("words", [])
        words = self._parse_words(raw_words)

        # Build segments from words (group by speaker changes)
        segments = self._build_segments(words)

        # Count unique speakers
        speakers_detected = len(set(w.speaker for w in words if w.speaker))

        return Transcript(
            audio_file=audio_path,
            language=language,
            text=full_text,
            segments=segments,
            processing_time=processing_time,
            model_used=self.model,
            timestamp=time.time(),
            diarization_enabled=self.enable_diarization,
            speakers_detected=speakers_detected if speakers_detected > 0 else None,
            provider_metadata={
                "provider": "elevenlabs",
                "language_probability": response_data.get("language_probability"),
                "transcription_id": response_data.get("transcription_id"),
            },
        )

    def _parse_words(self, raw_words: List[Dict[str, Any]]) -> List[Word]:
        """
        Parse words from ElevenLabs response.

        Args:
            raw_words: List of word dictionaries from API response

        Returns:
            List of Word objects
        """
        words = []
        speaker_mapping: Dict[str, str] = {}  # Map ElevenLabs speaker_id to SPEAKER_XX

        for word_data in raw_words:
            # Skip non-word entries (spacing, etc.) unless it's an audio event we want
            word_type = word_data.get("type", "word")
            if word_type == "spacing":
                continue

            # Handle audio events
            if word_type == "audio_event":
                # Include audio events in square brackets
                text = f"[{word_data.get('text', 'event')}]"
            else:
                text = word_data.get("text", "")

            # Get speaker ID and map to standard format
            elevenlabs_speaker = word_data.get("speaker_id")
            speaker = None
            if elevenlabs_speaker:
                if elevenlabs_speaker not in speaker_mapping:
                    speaker_num = len(speaker_mapping) + 1
                    speaker_mapping[elevenlabs_speaker] = f"SPEAKER_{speaker_num:02d}"
                speaker = speaker_mapping[elevenlabs_speaker]

            # Get confidence/probability
            probability = word_data.get("logprob")
            if probability is not None:
                # Convert log probability to probability (0-1)
                import math

                probability = math.exp(probability) if probability < 0 else probability

            words.append(
                Word(
                    word=text,
                    start=word_data.get("start"),
                    end=word_data.get("end"),
                    probability=probability,
                    speaker=speaker,
                )
            )

        return words

    def _build_segments(self, words: List[Word]) -> List[Segment]:
        """
        Build segments from words, grouping by speaker changes.

        Args:
            words: List of Word objects with timestamps and speakers

        Returns:
            List of Segment objects
        """
        if not words:
            return []

        segments = []
        segment_id = 0
        current_speaker = words[0].speaker
        current_words: List[Word] = []
        segment_start = words[0].start or 0.0

        for word in words:
            # Check for speaker change
            if word.speaker != current_speaker and current_words:
                # Save current segment
                segment_text = " ".join(w.word for w in current_words if w.word)
                segment_end = current_words[-1].end or current_words[-1].start or segment_start

                segments.append(
                    Segment(
                        id=segment_id,
                        start=segment_start,
                        end=segment_end,
                        text=segment_text.strip(),
                        speaker=current_speaker,
                        words=current_words,
                    )
                )
                segment_id += 1

                # Start new segment
                current_speaker = word.speaker
                current_words = []
                segment_start = word.start or segment_end

            current_words.append(word)

        # Save final segment
        if current_words:
            segment_text = " ".join(w.word for w in current_words if w.word)
            segment_end = current_words[-1].end or current_words[-1].start or segment_start

            segments.append(
                Segment(
                    id=segment_id,
                    start=segment_start,
                    end=segment_end,
                    text=segment_text.strip(),
                    speaker=current_speaker,
                    words=current_words,
                )
            )

        return segments

    def _save_transcript(self, transcript: Transcript, output_path: str) -> None:
        """Save transcript to JSON file."""
        import json

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Transcript saved to {output_path}")

    # =========================================================================
    # Pending Operation Persistence (for resume capability)
    # =========================================================================

    def _get_pending_operation_path(self, transcription_id: str) -> Optional[Path]:
        """Get path to pending operation file."""
        if not self.path_manager:
            return None
        return self.path_manager.pending_operations_dir() / f"elevenlabs_{transcription_id}.json"

    def _save_pending_operation(
        self,
        transcription_id: str,
        audio_path: str,
        language: str,
        episode_id: str,
        podcast_slug: Optional[str],
        episode_slug: Optional[str],
    ) -> None:
        """
        Save pending operation to disk for resume capability.

        If the app crashes during transcription, this file allows resuming
        by polling for the transcription_id.
        """
        import json

        op_path = self._get_pending_operation_path(transcription_id)
        if not op_path:
            logger.debug("No path_manager, skipping operation persistence")
            return

        op_path.parent.mkdir(parents=True, exist_ok=True)

        operation_data = {
            "provider": "elevenlabs",
            "transcription_id": transcription_id,
            "audio_path": audio_path,
            "language": language,
            "episode_id": episode_id,
            "podcast_slug": podcast_slug,
            "episode_slug": episode_slug,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": "pending",
        }

        with open(op_path, "w", encoding="utf-8") as f:
            json.dump(operation_data, f, indent=2)

        logger.debug(f"Saved pending operation: {op_path}")

    def _remove_pending_operation(self, transcription_id: str) -> None:
        """Remove pending operation file after successful completion."""
        op_path = self._get_pending_operation_path(transcription_id)
        if op_path and op_path.exists():
            op_path.unlink()
            logger.debug(f"Removed pending operation: {op_path}")

    def resume_pending_operations(self) -> List[Dict[str, Any]]:
        """
        Find and resume any pending ElevenLabs transcription operations.

        Call this on startup to check for operations that were interrupted.

        Returns:
            List of completed transcription results (as dicts with episode_id, transcript)
        """
        import json

        if not self.path_manager:
            return []

        pending_dir = self.path_manager.pending_operations_dir()
        if not pending_dir.exists():
            return []

        results = []
        for op_file in pending_dir.glob("elevenlabs_*.json"):
            try:
                with open(op_file, "r", encoding="utf-8") as f:
                    op_data = json.load(f)

                transcription_id = op_data.get("transcription_id")
                if not transcription_id:
                    continue

                logger.info(f"Found pending operation: {transcription_id}")

                # Try to get the transcript
                response_data = self._poll_for_transcript(transcription_id)

                if response_data:
                    # Format and return the transcript
                    transcript = self._format_response(
                        response_data,
                        op_data.get("audio_path", ""),
                        time.time(),  # We don't have original start time
                    )

                    results.append(
                        {
                            "episode_id": op_data.get("episode_id"),
                            "podcast_slug": op_data.get("podcast_slug"),
                            "episode_slug": op_data.get("episode_slug"),
                            "transcript": transcript,
                        }
                    )

                    # Clean up the pending file
                    op_file.unlink()
                    logger.info(f"Resumed and completed: {transcription_id}")
                else:
                    logger.warning(f"Could not retrieve transcript for {transcription_id}")

            except Exception as e:
                logger.error(f"Error resuming operation {op_file}: {e}")

        return results

    def list_pending_operations(self) -> List[Dict[str, Any]]:
        """
        List all pending ElevenLabs transcription operations.

        Returns:
            List of pending operation metadata dicts
        """
        import json

        if not self.path_manager:
            return []

        pending_dir = self.path_manager.pending_operations_dir()
        if not pending_dir.exists():
            return []

        operations = []
        for op_file in pending_dir.glob("elevenlabs_*.json"):
            try:
                with open(op_file, "r", encoding="utf-8") as f:
                    op_data = json.load(f)
                operations.append(op_data)
            except Exception as e:
                logger.error(f"Error reading operation {op_file}: {e}")

        return operations
