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
Audio file integrity checks.

Validates downloaded audio files against a small allowlist of codec
magic bytes BEFORE we hand the path to ``ffprobe`` / ``ffmpeg``.
Polyglot / zip-bomb / archive files masquerading as ``.mp3`` are
rejected up front, so a hostile RSS feed cannot trigger the audio
toolchain on something it wasn't designed to parse.

The helper intentionally only knows about formats we actually want
to accept from podcast feeds.  Anything else is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple


class InvalidAudioFile(ValueError):
    """Raised when a downloaded file does not look like a supported audio codec."""


# Magic-byte prefixes for every format we will feed to ffmpeg.
# Each tuple is (offset, bytes, label).
_MAGIC_PATTERNS: Tuple[Tuple[int, bytes, str], ...] = (
    (0, b"ID3", "mp3-id3"),
    (0, b"\xff\xfb", "mp3"),  # MPEG-1 Layer 3, no CRC
    (0, b"\xff\xf3", "mp3"),  # MPEG-2 Layer 3
    (0, b"\xff\xf2", "mp3"),  # MPEG-2.5 Layer 3
    (0, b"\xff\xf1", "aac-adts"),  # AAC ADTS
    (0, b"\xff\xf9", "aac-adts"),  # AAC ADTS (protection absent)
    (0, b"RIFF", "wav"),  # Container; inner WAVE checked below
    (0, b"OggS", "ogg"),
    (0, b"fLaC", "flac"),
    (4, b"ftyp", "mp4-family"),  # m4a / mp4 / m4b
)


def _looks_like_audio(prefix: bytes) -> Optional[str]:
    """Return a label if ``prefix`` matches a supported codec, else ``None``."""
    for offset, needle, label in _MAGIC_PATTERNS:
        if prefix[offset : offset + len(needle)] == needle:
            # Extra check for WAV: the 'WAVE' fourcc must appear at byte 8.
            if label == "wav" and prefix[8:12] != b"WAVE":
                return None
            return label
    return None


def assert_audio_file(path: Path, *, min_bytes: int = 32) -> str:
    """
    Raise :class:`InvalidAudioFile` unless ``path`` starts with magic bytes
    we recognise as one of the supported audio codecs.

    Returns the detected codec label on success so callers can log it.
    """
    if not path.exists():
        raise InvalidAudioFile(f"audio file not found: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise InvalidAudioFile(f"audio file too small ({size} bytes): {path}")

    with path.open("rb") as fh:
        header = fh.read(16)
    label = _looks_like_audio(header)
    if label is None:
        raise InvalidAudioFile(
            f"file does not match any supported audio codec header: {path} " f"(first 16 bytes: {header.hex()})"
        )
    return label


__all__ = ["InvalidAudioFile", "assert_audio_file"]
