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
Structured transcript models for speech-to-text output.

These models provide type-safe representations of transcription results
from all supported transcribers (Whisper, WhisperX, Parakeet, Google Cloud).
"""

from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Word(BaseModel):
    """Word-level timestamp and metadata."""

    word: str
    start: Optional[float] = None
    end: Optional[float] = None
    probability: Optional[float] = None
    speaker: Optional[str] = None


class Segment(BaseModel):
    """A timestamped segment of transcript."""

    id: int
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    words: List[Word] = Field(default_factory=list)
    confidence: Optional[float] = None  # Google Cloud provides this


class Transcript(BaseModel):
    """
    Structured transcript output from any transcriber.

    This is the standard return type for all transcribe_audio() methods.
    All transcribers produce this same structure, though some fields
    may be None depending on the provider's capabilities.

    Example:
        transcript = transcriber.transcribe_audio("audio.wav")
        print(transcript.text)
        for segment in transcript.segments:
            print(f"[{segment.start:.1f}s] {segment.speaker}: {segment.text}")
    """

    # Core fields (always present)
    audio_file: str
    language: str
    text: str
    segments: List[Segment]
    processing_time: float
    model_used: str
    timestamp: float

    # Diarization info (optional)
    diarization_enabled: bool = False
    speakers_detected: Optional[int] = None

    # Cleaning info (populated after LLM cleaning)
    cleaned_text: Optional[str] = None
    cleaning_metadata: Optional[Dict[str, Any]] = None

    # Provider-specific metadata (e.g., Google request_id, billing info)
    provider_metadata: Optional[Dict[str, Any]] = None

    def get_text_with_timestamps(self) -> str:
        """
        Extract plain text with timestamps from transcript.

        Returns formatted string like:
            [00:15] [SPEAKER_01] Welcome to the podcast.
            [00:18] [SPEAKER_02] Thanks for having me.
        """
        lines = []
        for segment in self.segments:
            text = segment.text.strip()
            if text:
                mins, secs = int(segment.start // 60), int(segment.start % 60)
                if segment.speaker:
                    lines.append(f"[{mins:02d}:{secs:02d}] [{segment.speaker}] {text}")
                else:
                    lines.append(f"[{mins:02d}:{secs:02d}] {text}")
        return "\n".join(lines)

    def get_speakers(self) -> List[str]:
        """Get list of unique speaker labels in order of appearance."""
        seen = set()
        speakers = []
        for segment in self.segments:
            if segment.speaker and segment.speaker not in seen:
                seen.add(segment.speaker)
                speakers.append(segment.speaker)
        return speakers

    def get_duration(self) -> float:
        """Get total audio duration in seconds based on segment timestamps."""
        if not self.segments:
            return 0.0
        return max(seg.end for seg in self.segments)

    def adjust_timestamps(self, offset_seconds: float) -> "Transcript":
        """
        Create a new Transcript with all timestamps shifted by offset.

        Args:
            offset_seconds: Amount to add to all timestamps (can be negative)

        Returns:
            New Transcript with adjusted timestamps
        """
        adjusted_segments = []
        for segment in self.segments:
            adjusted_words = [
                Word(
                    word=w.word,
                    start=w.start + offset_seconds if w.start is not None else None,
                    end=w.end + offset_seconds if w.end is not None else None,
                    probability=w.probability,
                    speaker=w.speaker,
                )
                for w in segment.words
            ]
            adjusted_segments.append(
                Segment(
                    id=segment.id,
                    start=segment.start + offset_seconds,
                    end=segment.end + offset_seconds,
                    text=segment.text,
                    speaker=segment.speaker,
                    words=adjusted_words,
                    confidence=segment.confidence,
                )
            )

        return Transcript(
            audio_file=self.audio_file,
            language=self.language,
            text=self.text,
            segments=adjusted_segments,
            processing_time=self.processing_time,
            model_used=self.model_used,
            timestamp=self.timestamp,
            diarization_enabled=self.diarization_enabled,
            speakers_detected=self.speakers_detected,
            cleaned_text=self.cleaned_text,
            cleaning_metadata=self.cleaning_metadata,
            provider_metadata=self.provider_metadata,
        )

    def apply_speaker_mapping(self, mapping: Dict[str, str]) -> "Transcript":
        """
        Create a new Transcript with speaker labels remapped.

        Args:
            mapping: Dict mapping old speaker IDs to new speaker IDs

        Returns:
            New Transcript with remapped speaker labels
        """
        if not mapping:
            return self.model_copy(deep=True)

        remapped_segments = []
        for segment in self.segments:
            remapped_words = [
                Word(
                    word=w.word,
                    start=w.start,
                    end=w.end,
                    probability=w.probability,
                    speaker=mapping.get(w.speaker, w.speaker) if w.speaker else None,
                )
                for w in segment.words
            ]
            new_speaker = mapping.get(segment.speaker, segment.speaker) if segment.speaker else None
            remapped_segments.append(
                Segment(
                    id=segment.id,
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    speaker=new_speaker,
                    words=remapped_words,
                    confidence=segment.confidence,
                )
            )

        return Transcript(
            audio_file=self.audio_file,
            language=self.language,
            text=self.text,
            segments=remapped_segments,
            processing_time=self.processing_time,
            model_used=self.model_used,
            timestamp=self.timestamp,
            diarization_enabled=self.diarization_enabled,
            speakers_detected=self.speakers_detected,
            cleaned_text=self.cleaned_text,
            cleaning_metadata=self.cleaning_metadata,
            provider_metadata=self.provider_metadata,
        )

    def get_words_in_range(self, start_sec: float, end_sec: float) -> List[Word]:
        """
        Extract all words within a time range.

        Args:
            start_sec: Start of range (seconds, inclusive)
            end_sec: End of range (seconds, inclusive)

        Returns:
            List of Word objects within the range
        """
        words = []
        for segment in self.segments:
            for word in segment.words:
                if word.start is not None and start_sec <= word.start <= end_sec:
                    words.append(word)
        return words

    def build_speaker_mapping(
        self,
        other: "Transcript",
        overlap_start: float,
        overlap_end: float,
        match_window_sec: float = 0.5,
        min_votes: int = 3,
    ) -> Dict[str, str]:
        """
        Build speaker mapping from self to other using overlap region.

        Analyzes matching words in the overlap region to determine which
        speakers in self correspond to which speakers in other.

        Args:
            other: The reference transcript (typically the previous chunk)
            overlap_start: Start of overlap region (seconds)
            overlap_end: End of overlap region (seconds)
            match_window_sec: Maximum time difference for word matching
            min_votes: Minimum matching words required for confident mapping

        Returns:
            Dict mapping speaker IDs in self to speaker IDs in other
        """
        self_words = self.get_words_in_range(overlap_start, overlap_end)
        other_words = other.get_words_in_range(overlap_start, overlap_end)

        if not self_words or not other_words:
            return {}

        # Count speaker co-occurrences via word matching
        speaker_votes: Counter = Counter()

        for self_word in self_words:
            if not self_word.speaker:
                continue

            # Find matching word in other transcript
            best_match = None
            best_time_diff = float("inf")

            for other_word in other_words:
                # Case-insensitive text match
                if self_word.word.lower() != other_word.word.lower():
                    continue

                # Timestamp proximity check
                if self_word.start is not None and other_word.start is not None:
                    time_diff = abs(self_word.start - other_word.start)
                    if time_diff < match_window_sec and time_diff < best_time_diff:
                        best_match = other_word
                        best_time_diff = time_diff

            if best_match and best_match.speaker:
                speaker_votes[(self_word.speaker, best_match.speaker)] += 1

        # Build mapping using majority voting
        self_speakers = set(w.speaker for w in self_words if w.speaker)
        speaker_mapping = {}

        for self_speaker in self_speakers:
            votes_for_speaker = {
                other_spk: count for (self_spk, other_spk), count in speaker_votes.items() if self_spk == self_speaker
            }

            if votes_for_speaker:
                best_other_speaker = max(votes_for_speaker, key=votes_for_speaker.get)
                vote_count = votes_for_speaker[best_other_speaker]

                if vote_count >= min_votes:
                    speaker_mapping[self_speaker] = best_other_speaker

        return speaker_mapping

    def merge(self, other: "Transcript", duplicate_window_sec: float = 0.5) -> "Transcript":
        """
        Merge this transcript with another, handling overlap deduplication.

        Words from both transcripts are combined, sorted by timestamp,
        and deduplicated (words within duplicate_window_sec with same text
        are considered duplicates). Segments are rebuilt based on speaker
        changes.

        Args:
            other: The transcript to merge with
            duplicate_window_sec: Time window for detecting duplicate words

        Returns:
            New merged Transcript
        """
        # Collect all words from both transcripts
        all_words = []
        for segment in self.segments:
            for word in segment.words:
                all_words.append(word)
        for segment in other.segments:
            for word in segment.words:
                all_words.append(word)

        # Sort by start time
        all_words.sort(key=lambda w: w.start if w.start is not None else 0.0)

        # Deduplicate words in overlap regions
        deduplicated_words = []
        for word in all_words:
            is_duplicate = False
            # Check against recent words for duplicates
            for existing in deduplicated_words[-10:]:
                if word.start is not None and existing.start is not None:
                    time_diff = abs(word.start - existing.start)
                    if time_diff < duplicate_window_sec and word.word.lower() == existing.word.lower():
                        is_duplicate = True
                        break
            if not is_duplicate:
                deduplicated_words.append(word)

        # Handle empty result
        if not deduplicated_words:
            return Transcript(
                audio_file=self.audio_file,
                language=self.language,
                text="",
                segments=[],
                processing_time=self.processing_time,
                model_used=self.model_used,
                timestamp=self.timestamp,
                diarization_enabled=self.diarization_enabled,
                speakers_detected=0,
            )

        # Rebuild segments grouped by speaker changes
        segments = []
        speakers_detected = set()
        segment_id = 0

        current_speaker = deduplicated_words[0].speaker
        current_words: List[Word] = []
        current_start = deduplicated_words[0].start or 0.0

        for word in deduplicated_words:
            if word.speaker:
                speakers_detected.add(word.speaker)

            # Speaker change triggers new segment
            if word.speaker != current_speaker and current_words:
                segment_text = " ".join(w.word for w in current_words)
                last_word_end = current_words[-1].end or current_words[-1].start or current_start
                segments.append(
                    Segment(
                        id=segment_id,
                        start=current_start,
                        end=last_word_end,
                        text=segment_text,
                        speaker=current_speaker,
                        words=current_words,
                    )
                )
                segment_id += 1

                # Start new segment
                current_speaker = word.speaker
                current_words = []
                current_start = word.start or 0.0

            current_words.append(word)

        # Save final segment
        if current_words:
            segment_text = " ".join(w.word for w in current_words)
            last_word_end = current_words[-1].end or current_words[-1].start or current_start
            segments.append(
                Segment(
                    id=segment_id,
                    start=current_start,
                    end=last_word_end,
                    text=segment_text,
                    speaker=current_speaker,
                    words=current_words,
                )
            )

        # Build full text
        full_text = " ".join(seg.text for seg in segments)

        return Transcript(
            audio_file=self.audio_file,
            language=self.language,
            text=full_text,
            segments=segments,
            processing_time=self.processing_time,
            model_used=self.model_used,
            timestamp=self.timestamp,
            diarization_enabled=self.diarization_enabled,
            speakers_detected=len(speakers_detected) if speakers_detected else None,
        )
