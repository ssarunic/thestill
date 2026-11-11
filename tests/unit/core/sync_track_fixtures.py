"""Synthetic MP3 byte streams for sync track tests (spec #116).

The sync track code only reads frame headers and treats frame payloads as
opaque bytes, so a "recording" here is a run of valid Layer III headers
(MPEG-1, 128 kbps, 44.1 kHz: 417-byte frames, 418 with the padding bit)
followed by seeded random payload. No encoder is needed.
"""

from __future__ import annotations

import random
from typing import Iterator, List

FRAME_SECONDS = 1152 / 44100


def frames(seed: int, count: int) -> List[bytes]:
    """``count`` distinct frames; the same seed gives the same frames."""
    rng = random.Random(seed)
    out = []
    for index in range(count):
        padded = index % 49 == 0  # mimic the occasional padded frame
        header = bytes([0xFF, 0xFB, 0x92 if padded else 0x90, 0x00])
        out.append(header + rng.randbytes((418 if padded else 417) - 4))
    return out


def silence(count: int) -> List[bytes]:
    """``count`` identical frames, as digital silence encodes."""
    return [bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)] * count


def seconds(n: float) -> int:
    """Frames in ``n`` seconds."""
    return int(round(n / FRAME_SECONDS))


def id3(size: int = 300) -> bytes:
    """An ID3v2 tag whose body is full of ``0xFF 0xFB`` look-alike headers."""
    body = (bytes([0xFF, 0xFB, 0x90, 0x00]) * (size // 4 + 1))[:size]
    synchsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x04\x00\x00" + synchsafe + body


def stream(*parts) -> bytes:
    """Concatenate frame lists and raw byte strings."""
    out = bytearray()
    for part in parts:
        out += part if isinstance(part, (bytes, bytearray)) else b"".join(part)
    return bytes(out)


def chunks(data: bytes, size: int = 7919) -> Iterator[bytes]:
    """Feed bytes in awkward chunk sizes so frames straddle chunk edges."""
    for start in range(0, len(data), size):
        yield data[start : start + size]


EPISODE_ID = "11111111-1111-4111-8111-111111111111"
PODCAST_ID = "22222222-2222-4222-8222-222222222222"


def make_sqlite_sync_track_repo(tmp_path):
    """A SQLite sync track repository with one podcast and one episode
    (``EPISODE_ID``) so the foreign key holds."""
    from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from thestill.repositories.sqlite_sync_track_repository import SqliteSyncTrackRepository
    from thestill.utils.sqlite_ext import connect

    db = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db)  # creates the schema
    now = "2026-11-10T00:00:00+00:00"
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, created_at, updated_at)"
            " VALUES (?, 'https://example.com/rss', 'Show', 'the-show', ?, ?)",
            (PODCAST_ID, now, now),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, created_at, updated_at)"
            " VALUES (?, ?, 'ext-1', 'Episode', 'ep', 'https://example.com/a.mp3', ?, ?)",
            (EPISODE_ID, PODCAST_ID, now, now),
        )
    return SqliteSyncTrackRepository(db_path=db)


class RecordingLogger:
    """Stand-in for a module's structlog logger.

    ``structlog.testing.capture_logs`` misses loggers that earlier tests
    cached, so tests monkeypatch the module-level ``logger`` with this.
    Each call is stored as a dict with ``event``, ``log_level`` and the
    bound and passed fields.
    """

    def __init__(self, events=None, context=None):
        self.events = [] if events is None else events
        self._context = dict(context or {})

    def bind(self, **fields):
        return RecordingLogger(self.events, {**self._context, **fields})

    def _log(self, level, event, **fields):
        fields.pop("exc_info", None)
        self.events.append({**self._context, **fields, "event": event, "log_level": level})

    def debug(self, event, **fields):
        self._log("debug", event, **fields)

    def info(self, event, **fields):
        self._log("info", event, **fields)

    def warning(self, event, **fields):
        self._log("warning", event, **fields)

    def error(self, event, **fields):
        self._log("error", event, **fields)

    def named(self, name):
        return [entry for entry in self.events if entry["event"] == name]
