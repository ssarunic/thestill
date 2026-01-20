#!/usr/bin/env python3
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

"""Transcription options and related types."""

from dataclasses import dataclass
from typing import Callable


@dataclass
class TranscribeOptions:
    """Options for transcription.

    Attributes:
        language: Language code for transcription (e.g., "en", "es").
        episode_id: Episode ID for operation persistence (used by Google/ElevenLabs).
        podcast_slug: Podcast slug for operation persistence.
        episode_slug: Episode slug for operation persistence.
        progress_callback: Callback function for progress updates (0.0 to 100.0).
    """

    language: str

    # Episode context (for operation persistence in Google/ElevenLabs)
    episode_id: str | None = None
    podcast_slug: str | None = None
    episode_slug: str | None = None

    # Progress reporting
    progress_callback: Callable[[float], None] | None = None
