# Copyright 2025-2026 Thestill
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
Briefing model for THES-153: Briefing persistence.

Represents a generated briefing document with metadata about included episodes,
processing statistics, and status tracking.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class BriefingStatus(str, Enum):
    """
    Briefing generation status.

    - PENDING: Briefing generation has been requested but not started
    - IN_PROGRESS: Episodes are being processed
    - COMPLETED: All episodes processed successfully
    - PARTIAL: Some episodes failed, briefing generated with available content
    - FAILED: Briefing generation failed completely
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Briefing(BaseModel):
    """
    Represents a generated briefing document.

    A briefing is a consolidated view of processed podcast episodes, typically
    generated for a specific time period (e.g., "morning briefing").

    Attributes:
        id: Unique identifier (UUID)
        user_id: User who created this briefing (required, uses default user in CLI mode)
        created_at: When the briefing was created
        updated_at: When the briefing was last updated
        period_start: Start of the time period covered by this briefing
        period_end: End of the time period covered by this briefing
        status: Current status of briefing generation
        file_path: Path to the generated markdown file (relative to briefings dir)
        episode_ids: List of episode IDs included in this briefing
        episodes_total: Total number of episodes selected for processing
        episodes_completed: Number of episodes successfully processed
        episodes_failed: Number of episodes that failed processing
        processing_time_seconds: Total time taken for processing
        error_message: Error message if status is FAILED
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str  # User who owns this briefing (required, uses default user in CLI mode)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Time period covered
    period_start: datetime
    period_end: datetime

    # Status tracking
    status: BriefingStatus = BriefingStatus.PENDING

    # Output file
    file_path: Optional[str] = None

    # Episode tracking (list of episode UUIDs)
    episode_ids: List[str] = Field(default_factory=list)

    # Processing statistics
    episodes_total: int = 0
    episodes_completed: int = 0
    episodes_failed: int = 0
    processing_time_seconds: Optional[float] = None

    # Error tracking
    error_message: Optional[str] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.episodes_total == 0:
            return 0.0
        return (self.episodes_completed / self.episodes_total) * 100

    @property
    def is_complete(self) -> bool:
        """Check if briefing generation is complete (success or failure)."""
        return self.status in (
            BriefingStatus.COMPLETED,
            BriefingStatus.PARTIAL,
            BriefingStatus.FAILED,
        )

    def mark_in_progress(self) -> None:
        """Mark briefing as in progress."""
        self.status = BriefingStatus.IN_PROGRESS
        self.updated_at = datetime.now(timezone.utc)

    def mark_completed(
        self,
        file_path: str,
        episodes_completed: int,
        episodes_failed: int,
        processing_time_seconds: float,
    ) -> None:
        """Mark briefing as completed with results."""
        self.file_path = file_path
        self.episodes_completed = episodes_completed
        self.episodes_failed = episodes_failed
        self.processing_time_seconds = processing_time_seconds
        self.updated_at = datetime.now(timezone.utc)

        if episodes_failed == 0:
            self.status = BriefingStatus.COMPLETED
        elif episodes_completed > 0:
            self.status = BriefingStatus.PARTIAL
        else:
            self.status = BriefingStatus.FAILED

    def mark_failed(self, error_message: str) -> None:
        """Mark briefing as failed."""
        self.status = BriefingStatus.FAILED
        self.error_message = error_message
        self.updated_at = datetime.now(timezone.utc)
