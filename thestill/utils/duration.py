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

"""Duration parsing and formatting utilities."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


def parse_duration(duration: Optional[str]) -> Optional[int]:
    """
    Parse duration string to seconds.

    Handles multiple formats:
    - Integer seconds: "3600" -> 3600
    - Float seconds: "3600.5" -> 3600
    - MM:SS format: "45:30" -> 2730
    - HH:MM:SS format: "01:23:45" -> 5025

    Args:
        duration: Duration string in various formats

    Returns:
        Duration in seconds as integer, or None if parsing fails
    """
    if duration is None:
        return None

    duration = duration.strip()
    if not duration:
        return None

    # Try parsing as numeric (seconds)
    try:
        return int(float(duration))
    except ValueError:
        pass

    # Try parsing as HH:MM:SS or MM:SS
    parts = duration.split(":")
    try:
        if len(parts) == 2:
            # MM:SS
            minutes, seconds = int(parts[0]), int(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:
            # HH:MM:SS
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        pass

    logger.warning(f"Could not parse duration: {duration}")
    return None


def format_duration(seconds: Union[int, float]) -> str:
    """
    Format seconds as human-readable duration.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "1:23:45" or "45:30"
    """
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_duration_verbose(seconds: Union[int, float]) -> str:
    """
    Format seconds as verbose human-readable duration.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "1h 23m 45s" or "45m 30s"
    """
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")

    return " ".join(parts)


def get_audio_duration(audio_path: Union[str, Path]) -> Optional[int]:
    """
    Get audio file duration using ffprobe.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds as integer, or None if ffprobe fails
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        logger.warning(f"Audio file not found: {audio_path}")
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.warning(f"ffprobe failed for {audio_path}: {result.stderr}")
            return None

        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration")
        if duration_str:
            return int(float(duration_str))

    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timed out for {audio_path}")
    except json.JSONDecodeError as e:
        logger.warning(f"ffprobe output parse error for {audio_path}: {e}")
    except FileNotFoundError:
        logger.warning("ffprobe not found - please install ffmpeg")
    except Exception as e:
        logger.warning(f"Error getting duration for {audio_path}: {e}")

    return None


def calculate_speed_ratio(processing_seconds: Union[int, float], audio_seconds: Union[int, float]) -> Optional[float]:
    """
    Calculate processing speed as ratio of realtime.

    Args:
        processing_seconds: Time spent processing
        audio_seconds: Duration of audio content

    Returns:
        Speed ratio (e.g., 0.5 means 2x realtime, 2.0 means 0.5x realtime)
        Returns None if audio_seconds is 0 or negative
    """
    if audio_seconds <= 0:
        return None
    return processing_seconds / audio_seconds


def format_speed_stats(processing_seconds: Union[int, float], audio_seconds: Union[int, float]) -> str:
    """
    Format processing speed statistics.

    Args:
        processing_seconds: Time spent processing
        audio_seconds: Duration of audio content

    Returns:
        Formatted string like "0.29x realtime (17.4 min/hr of audio)"
    """
    ratio = calculate_speed_ratio(processing_seconds, audio_seconds)
    if ratio is None:
        return "unknown speed"

    # Minutes needed per hour of audio
    minutes_per_hour = ratio * 60

    return f"{ratio:.2f}x realtime ({minutes_per_hour:.1f} min/hr of audio)"
