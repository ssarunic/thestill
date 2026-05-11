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
Thestill - Automated Podcast Transcription and Summarization Pipeline
"""

__version__ = "1.0.0"
__author__ = "Your Name"
__description__ = "Automated podcast transcription and summarization pipeline"

# Ensure ffmpeg/ffprobe are on PATH before any subprocess or pydub call runs.
# Some launch contexts (e.g. stale shell snapshots) omit /opt/homebrew/bin.
from .utils.ffmpeg_path import ensure_ffmpeg_on_path as _ensure_ffmpeg_on_path

_ensure_ffmpeg_on_path()
