# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Ensure ffmpeg/ffprobe are reachable from os.environ['PATH'].

The pipeline depends on ffmpeg (via pydub) and ffprobe (via direct subprocess
in utils/duration.py). When the app is launched from a shell whose PATH does
not include the directory containing them (e.g. a stale shell snapshot that
omits /opt/homebrew/bin), pydub falls back to bare "ffmpeg"/"ffprobe" and
fails with FileNotFoundError mid-pipeline.

ensure_ffmpeg_on_path() probes a small set of well-known install locations
and prepends the first match to PATH if neither tool is currently resolvable.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_KNOWN_DIRS = (
    "/opt/homebrew/bin",  # Homebrew on Apple Silicon
    "/usr/local/bin",  # Homebrew on Intel macOS, common Linux installs
    "/opt/local/bin",  # MacPorts
)


def ensure_ffmpeg_on_path() -> str | None:
    """
    Make sure both ffmpeg and ffprobe are resolvable via PATH.

    If they are already, returns None. Otherwise probes _KNOWN_DIRS for a
    directory containing both binaries and prepends it to os.environ['PATH'].
    Returns the directory it added, or None if nothing was changed (either
    because PATH was already fine or no known directory had both tools).
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return None

    for candidate in _KNOWN_DIRS:
        ffmpeg = Path(candidate) / "ffmpeg"
        ffprobe = Path(candidate) / "ffprobe"
        if ffmpeg.exists() and ffprobe.exists():
            current = os.environ.get("PATH", "")
            if candidate not in current.split(os.pathsep):
                os.environ["PATH"] = candidate + os.pathsep + current if current else candidate
            return candidate

    return None
