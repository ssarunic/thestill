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
Webhook completion tracker for coordinating CLI and webhook handlers.

This module provides a thread-safe mechanism to track pending webhook
callbacks and signal when all expected callbacks have been received.
"""

import threading
from typing import Dict, Optional, Set

from structlog import get_logger

logger = get_logger(__name__)


class WebhookTracker:
    """
    Thread-safe tracker for pending webhook callbacks.

    Used to coordinate between the CLI (which submits transcriptions) and
    the webhook handler (which receives callbacks). When all expected
    callbacks arrive, the CLI can exit gracefully.

    Note: We track by episode_id since ElevenLabs returns a different request_id
    in the webhook callback than the transcription_id from the submission response.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: Set[str] = set()  # Set of pending episode IDs
        self._completed: Dict[str, bool] = {}  # episode_id -> success
        self._all_done = threading.Event()

    def add_pending(self, episode_id: str) -> None:
        """Register an episode as pending (waiting for webhook callback)."""
        with self._lock:
            self._pending.add(episode_id)
            self._all_done.clear()
            logger.debug("Added pending episode", episode_id=episode_id, total_pending=len(self._pending))

    def mark_completed(self, episode_id: str, success: bool = True) -> None:
        """Mark an episode as completed (callback received)."""
        with self._lock:
            if episode_id in self._pending:
                self._pending.discard(episode_id)
                self._completed[episode_id] = success
                logger.info(
                    "Episode transcription completed",
                    episode_id=episode_id,
                    success=success,
                    remaining=len(self._pending),
                )
                if not self._pending:
                    logger.info("All pending transcriptions completed!")
                    self._all_done.set()
            else:
                # Callback for unknown episode (maybe from another session)
                logger.debug("Received callback for unknown episode", episode_id=episode_id)

    @property
    def pending_count(self) -> int:
        """Number of transcriptions still waiting for callbacks."""
        with self._lock:
            return len(self._pending)

    @property
    def completed_count(self) -> int:
        """Number of transcriptions that have completed."""
        with self._lock:
            return len(self._completed)

    @property
    def is_all_done(self) -> bool:
        """True if all pending transcriptions have completed."""
        with self._lock:
            return len(self._pending) == 0 and len(self._completed) > 0

    def wait_for_all(self, timeout: Optional[float] = None) -> bool:
        """
        Block until all pending transcriptions complete.

        Args:
            timeout: Maximum seconds to wait (None = wait forever)

        Returns:
            True if all completed, False if timeout occurred
        """
        # If nothing pending, return immediately
        with self._lock:
            if not self._pending:
                return True

        return self._all_done.wait(timeout=timeout)

    def reset(self) -> None:
        """Clear all tracking state."""
        with self._lock:
            self._pending.clear()
            self._completed.clear()
            self._all_done.clear()


# Global singleton instance for cross-module coordination
_tracker: Optional[WebhookTracker] = None
_tracker_lock = threading.Lock()


def get_tracker() -> WebhookTracker:
    """Get the global webhook tracker instance."""
    global _tracker
    with _tracker_lock:
        if _tracker is None:
            _tracker = WebhookTracker()
        return _tracker


def reset_tracker() -> None:
    """Reset the global tracker (for testing or new sessions)."""
    global _tracker
    with _tracker_lock:
        if _tracker is not None:
            _tracker.reset()
