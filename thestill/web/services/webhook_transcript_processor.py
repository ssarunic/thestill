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
Webhook transcript processor for converting ElevenLabs webhook payloads
to the standard Transcript format.

This service processes incoming webhook transcripts and:
1. Converts the payload to our standard Transcript model
2. Saves the transcript to the raw_transcripts directory
3. Updates the episode's raw_transcript_path in the database
"""

import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from thestill.models.transcript import Segment, Transcript, Word
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.path_manager import PathManager

logger = logging.getLogger(__name__)


class WebhookTranscriptProcessor:
    """
    Processes ElevenLabs webhook payloads into standard Transcript format.

    This processor mirrors the format conversion logic from ElevenLabsTranscriber
    to ensure consistency between direct API transcriptions and webhook callbacks.
    """

    def __init__(
        self,
        path_manager: PathManager,
        repository: SqlitePodcastRepository,
    ):
        """
        Initialize the webhook transcript processor.

        Args:
            path_manager: PathManager for transcript file paths
            repository: Repository for updating episode records
        """
        self.path_manager = path_manager
        self.repository = repository

    def process_elevenlabs_webhook(
        self,
        payload: Dict[str, Any],
    ) -> Optional[Transcript]:
        """
        Process an ElevenLabs webhook payload and save as raw transcript.

        This method:
        1. Extracts transcript data from the webhook payload
        2. Converts it to the standard Transcript model
        3. Saves it to the raw_transcripts directory
        4. Updates the episode's raw_transcript_path in the database
        5. Optionally cleans up the pending operation file

        Args:
            payload: The webhook payload from ElevenLabs

        Returns:
            Transcript object if successful, None if missing required data
        """
        # Extract metadata
        metadata = payload.get("webhook_metadata", {})
        episode_id = metadata.get("episode_id")
        podcast_slug = metadata.get("podcast_slug")
        episode_slug = metadata.get("episode_slug")
        transcription_id = payload.get("transcription_id") or payload.get("request_id")

        # ElevenLabs nests transcript data inside "transcription" key
        # Structure: {"request_id": ..., "transcription": {"text": ..., "words": ...}, "webhook_metadata": ...}
        transcription_data = payload.get("transcription", {})
        if isinstance(transcription_data, dict) and transcription_data:
            # Use nested transcription data
            transcript_text = transcription_data.get("text")
        else:
            # Fallback to top-level (older format)
            transcript_text = payload.get("text")
            transcription_data = payload  # Use payload directly

        if not episode_id:
            logger.warning("Webhook payload missing episode_id in metadata, skipping processing")
            return None

        if not transcript_text:
            logger.warning(f"Webhook payload for {transcription_id} has no transcript text")
            return None

        # Get the episode from the database
        # get_episode returns (Podcast, Episode) tuple or None
        episode_result = self.repository.get_episode(episode_id)
        if not episode_result:
            logger.error(f"Episode {episode_id} not found in database")
            return None

        podcast, episode = episode_result

        # Get podcast for slug fallback
        if not podcast_slug:
            podcast_slug = self._slugify(podcast.title)

        if not episode_slug:
            episode_slug = self._slugify(episode.title)

        logger.info(f"Processing webhook transcript for episode: {episode.title}")

        # Convert payload to Transcript
        transcript = self._format_webhook_response(
            payload,
            audio_file=episode.downsampled_audio_path or episode.audio_path or "",
        )

        # Determine output path
        # Use raw_transcript_file_with_podcast(podcast_slug, episode_filename)
        output_path = self.path_manager.raw_transcript_file_with_podcast(
            podcast_slug or "unknown", f"{episode_slug}.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save transcript
        self._save_transcript(transcript, str(output_path))

        # Update episode in database
        episode.raw_transcript_path = str(output_path)
        self.repository.update_episode(episode)

        logger.info(f"Saved webhook transcript to {output_path}")

        # Clean up pending operation file if it exists
        self._cleanup_pending_operation(transcription_id)

        return transcript

    def _format_webhook_response(
        self,
        payload: Dict[str, Any],
        audio_file: str,
    ) -> Transcript:
        """
        Convert ElevenLabs webhook payload to Transcript model.

        This mirrors the _format_response logic from ElevenLabsTranscriber
        to ensure consistent transcript format.

        Args:
            payload: Webhook payload with transcript data
            audio_file: Path to the original audio file

        Returns:
            Formatted Transcript object
        """
        # ElevenLabs nests transcript data inside "transcription" key
        transcription_data = payload.get("transcription", {})
        if not isinstance(transcription_data, dict) or not transcription_data:
            # Fallback to top-level (older format)
            transcription_data = payload

        # Extract language
        language = transcription_data.get("language_code", "en")

        # Extract full text
        full_text = transcription_data.get("text", "")

        # Build words list from response
        raw_words = transcription_data.get("words", [])
        words = self._parse_words(raw_words)

        # Build segments from words (group by speaker changes)
        segments = self._build_segments(words)

        # Count unique speakers
        speakers_detected = len(set(w.speaker for w in words if w.speaker))

        return Transcript(
            audio_file=audio_file,
            language=language,
            text=full_text,
            segments=segments,
            processing_time=0.0,  # Unknown for webhook
            model_used="scribe_v1",  # Default ElevenLabs model
            timestamp=time.time(),
            diarization_enabled=speakers_detected > 0,
            speakers_detected=speakers_detected if speakers_detected > 0 else None,
            provider_metadata={
                "provider": "elevenlabs",
                "source": "webhook",
                "language_probability": transcription_data.get("language_probability"),
                "transcription_id": payload.get("transcription_id") or payload.get("request_id"),
                "webhook_received_at": payload.get("_webhook_received_at"),
            },
        )

    def _parse_words(self, raw_words: List[Dict[str, Any]]) -> List[Word]:
        """
        Parse words from ElevenLabs webhook payload.

        This mirrors the _parse_words logic from ElevenLabsTranscriber.

        Args:
            raw_words: List of word dictionaries from webhook payload

        Returns:
            List of Word objects
        """
        words = []
        speaker_mapping: Dict[str, str] = {}  # Map ElevenLabs speaker_id to SPEAKER_XX

        for word_data in raw_words:
            # Skip non-word entries (spacing, etc.) unless it's an audio event
            word_type = word_data.get("type", "word")
            if word_type == "spacing":
                continue

            # Handle audio events
            if word_type == "audio_event":
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

        This mirrors the _build_segments logic from ElevenLabsTranscriber.

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
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Transcript saved to {output_path}")

    def _cleanup_pending_operation(self, transcription_id: Optional[str]) -> None:
        """Clean up pending operation file if it exists."""
        if not transcription_id:
            return

        pending_path = self.path_manager.pending_operations_dir() / f"elevenlabs_{transcription_id}.json"
        if pending_path.exists():
            pending_path.unlink()
            logger.info(f"Cleaned up pending operation: {transcription_id}")

    def _slugify(self, text: str) -> str:
        """
        Convert text to a URL-friendly slug.

        Args:
            text: The text to slugify

        Returns:
            A lowercase, hyphenated slug
        """
        import re

        # Convert to lowercase
        slug = text.lower()
        # Replace spaces and underscores with hyphens
        slug = re.sub(r"[\s_]+", "-", slug)
        # Remove non-alphanumeric characters except hyphens
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        # Remove multiple consecutive hyphens
        slug = re.sub(r"-+", "-", slug)
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        return slug or "untitled"
