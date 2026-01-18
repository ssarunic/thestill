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
Progress tracking types for transcription pipeline.

This module defines the data structures and protocols for reporting
progress during long-running operations like transcription.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class TranscriptionStage(str, Enum):
    """Stages of the transcription pipeline."""

    PENDING = "pending"
    UPLOADING = "uploading"
    LOADING_MODEL = "loading_model"
    TRANSCRIBING = "transcribing"
    ALIGNING = "aligning"
    DIARIZING = "diarizing"
    FORMATTING = "formatting"
    COMPLETED = "completed"
    FAILED = "failed"


# Human-readable labels for each stage
STAGE_LABELS = {
    TranscriptionStage.PENDING: "Waiting to start...",
    TranscriptionStage.UPLOADING: "Uploading audio file...",
    TranscriptionStage.LOADING_MODEL: "Loading transcription model...",
    TranscriptionStage.TRANSCRIBING: "Transcribing audio...",
    TranscriptionStage.ALIGNING: "Aligning word timestamps...",
    TranscriptionStage.DIARIZING: "Identifying speakers...",
    TranscriptionStage.FORMATTING: "Formatting output...",
    TranscriptionStage.COMPLETED: "Transcription complete",
    TranscriptionStage.FAILED: "Transcription failed",
}


@dataclass
class ProgressUpdate:
    """
    Represents a progress update during transcription.

    Attributes:
        stage: Current stage of the transcription pipeline
        progress_pct: Overall progress percentage (0-100)
        message: Human-readable status message
        estimated_remaining_seconds: Estimated time remaining (if available)
    """

    stage: TranscriptionStage
    progress_pct: int
    message: str
    estimated_remaining_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "stage": self.stage.value,
            "progress_pct": self.progress_pct,
            "message": self.message,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
        }


# Type alias for progress callback function
# Callbacks receive a ProgressUpdate and return nothing
ProgressCallback = Callable[[ProgressUpdate], None]
