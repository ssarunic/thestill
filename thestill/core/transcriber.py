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
Abstract base class for speech-to-text transcribers.

All transcriber implementations (Whisper, WhisperX, Parakeet, Google Cloud)
inherit from this base class to ensure a consistent interface.
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from thestill.models.transcript import Transcript
from thestill.utils.device import resolve_device
from thestill.utils.duration import get_audio_duration_minutes


class Transcriber(ABC):
    """
    Abstract base class for speech-to-text transcribers.

    Provides common functionality for saving transcripts, getting audio duration,
    and resolving compute devices. Subclasses must implement transcribe_audio()
    and load_model().

    All transcribers return a Transcript object with structured data including
    segments, word-level timestamps, and optional speaker diarization.
    """

    @abstractmethod
    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        language: str,
        custom_prompt: Optional[str] = None,
    ) -> Optional[Transcript]:
        """
        Transcribe audio file and return structured transcript.

        Args:
            audio_path: Path to audio file
            output_path: Optional path to save transcript JSON
            language: Language code (e.g., 'en', 'en-US')
            custom_prompt: Optional prompt to improve accuracy (provider-specific)

        Returns:
            Transcript object with segments and metadata. None on error.
        """
        pass

    @abstractmethod
    def load_model(self) -> None:
        """Load/initialize the transcription model (lazy loading)."""
        pass

    def _save_transcript(self, transcript: Transcript, output_path: str) -> None:
        """Save transcript to JSON file."""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False)
            print(f"Transcript saved to: {output_path}")
        except Exception as e:
            print(f"Error saving transcript: {e}")

    def _get_audio_duration_minutes(self, audio_path: str) -> float:
        """Get audio duration in minutes using ffprobe."""
        return get_audio_duration_minutes(audio_path)

    def _resolve_device(self, device: str) -> str:
        """Resolve 'auto' device to actual device (cuda/mps/cpu)."""
        return resolve_device(device)
