"""Alignment test vectors shared with the browser aligner (spec #116, Phase 2).

The Python aligner (``thestill.core.sync_track``) and the TypeScript one
(``thestill/web/frontend/src/drift``) must agree bit for bit: same frame
parsing, same digests, same spans. Each case here is a *recipe* for two
synthetic MP3 streams, the transcribed copy and a listener's copy, plus what
the Python code makes of them. The recipes are built from a 32-bit xorshift
generator that both languages implement, so no audio is stored: the fixture
holds the recipes, the sync track Python built (base64) and the expected
alignment.

``tests/fixtures/sync_track/align_vectors.json`` is the committed output.
``test_sync_track_vectors.py`` fails when it is stale; regenerate with::

    SYNC_TRACK_WRITE_VECTORS=1 ./venv/bin/python -m pytest tests/unit/core/test_sync_track_vectors.py
"""

from __future__ import annotations

import base64
import zlib
from pathlib import Path
from typing import Any, Dict, Iterator, List

from thestill.core.sync_track import align, build_sync_track, window_digest

VECTORS_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "sync_track" / "align_vectors.json"

FRAME_SECONDS = 1152 / 44100


def _seconds(n: float) -> int:
    """Frames in ``n`` seconds."""
    return int(round(n / FRAME_SECONDS))


def xorshift_frames(seed: int, count: int) -> bytes:
    """``count`` MPEG-1 Layer III frames (128 kbps, 44.1 kHz) with payload from
    a 32-bit xorshift generator. Every 49th frame carries the padding bit.

    The generator state runs on across frames; each step yields four payload
    bytes, least significant first, and a frame's last step is truncated.
    """
    state = seed & 0xFFFFFFFF
    if state == 0:
        raise ValueError("seed must be non-zero")
    out = bytearray()
    for index in range(count):
        padded = index % 49 == 0
        out += bytes([0xFF, 0xFB, 0x92 if padded else 0x90, 0x00])
        payload = bytearray()
        need = (418 if padded else 417) - 4
        while len(payload) < need:
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= state >> 17
            state ^= (state << 5) & 0xFFFFFFFF
            payload += state.to_bytes(4, "little")
        out += payload[:need]
    return bytes(out)


def silence_frames(count: int) -> bytes:
    """``count`` identical frames, as digital silence encodes."""
    return (bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)) * count


def id3_tag(size: int) -> bytes:
    """An ID3v2 tag whose body is full of frame-header look-alikes."""
    body = (bytes([0xFF, 0xFB, 0x90, 0x00]) * (size // 4 + 1))[:size]
    synchsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x04\x00\x00" + synchsafe + body


def render(parts: List[Dict[str, Any]]) -> bytes:
    """Turn a recipe into bytes. Part kinds: ``frames`` (seed, count, and an
    optional ``skip``/``take`` slice in frames), ``silence`` (count), ``id3``
    (size), ``junk`` (count bytes of 0x00)."""
    out = bytearray()
    for part in parts:
        kind = part["kind"]
        if kind == "frames":
            data = xorshift_frames(part["seed"], part["count"])
            if "skip" in part or "take" in part:
                data = _slice_frames(data, part.get("skip", 0), part.get("take"))
            out += data
        elif kind == "silence":
            out += silence_frames(part["count"])
        elif kind == "id3":
            out += id3_tag(part["size"])
        elif kind == "junk":
            out += bytes(part["count"])
        else:
            raise ValueError(f"unknown part kind {kind!r}")
    return bytes(out)


def _slice_frames(data: bytes, skip: int, take) -> bytes:
    """Frames ``skip .. skip+take`` of a run made by ``xorshift_frames``."""
    bounds = [0]
    index = 0
    while bounds[-1] < len(data):
        bounds.append(bounds[-1] + (418 if index % 49 == 0 else 417))
        index += 1
    end = len(bounds) - 1 if take is None else skip + take
    return data[bounds[skip] : bounds[end]]


def _chunks(data: bytes, size: int) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def _episode(seed: int, secs: float) -> Dict[str, Any]:
    return {"kind": "frames", "seed": seed, "count": _seconds(secs)}


def _part(seed: int, total_secs: float, from_s: float, to_s: float) -> Dict[str, Any]:
    """Seconds ``from_s .. to_s`` of the run ``_episode(seed, total_secs)``."""
    return {
        "kind": "frames",
        "seed": seed,
        "count": _seconds(total_secs),
        "skip": _seconds(from_s),
        "take": _seconds(to_s) - _seconds(from_s),
    }


def cases() -> List[Dict[str, Any]]:
    """The recipes. ``track`` is the transcribed copy, ``other`` a listener's."""
    show = 0x1234ABCD
    return [
        {
            "name": "identical copy",
            "track": [_episode(show, 30)],
            "other": [_episode(show, 30)],
        },
        {
            "name": "pre-roll added and a longer mid-roll",
            "track": [_part(show, 40, 0, 20), _episode(0x0A0A0A01, 6), _part(show, 40, 20, 40)],
            "other": [
                {"kind": "id3", "size": 300},
                _episode(0x0B0B0B02, 8),
                _part(show, 40, 0, 20),
                _episode(0x0C0C0C03, 15),
                _part(show, 40, 20, 40),
            ],
        },
        {
            "name": "ad only in the transcribed copy",
            "track": [_part(show, 30, 0, 15), _episode(0x0D0D0D04, 10), _part(show, 30, 15, 30)],
            "other": [_episode(show, 30)],
        },
        {
            "name": "ad replaced by another of the same length",
            "track": [_part(show, 40, 0, 20), _episode(0x0E0E0E05, 12), _part(show, 40, 20, 40)],
            "other": [_part(show, 40, 0, 20), _episode(0x0F0F0F06, 12), _part(show, 40, 20, 40)],
        },
        {
            "name": "promo played twice in the listener copy",
            "track": [_episode(0x11110007, 8), _episode(show, 30)],
            "other": [
                _episode(0x11110007, 8),
                _part(show, 30, 0, 15),
                _episode(0x11110007, 8),
                _part(show, 30, 15, 30),
            ],
        },
        {
            "name": "tags, junk and silence between segments",
            "track": [
                {"kind": "id3", "size": 1200},
                _part(show, 30, 0, 12),
                {"kind": "silence", "count": _seconds(3)},
                _part(show, 30, 12, 30),
            ],
            "other": [
                _episode(0x22220008, 5),
                {"kind": "id3", "size": 64},
                _part(show, 30, 0, 12),
                {"kind": "silence", "count": _seconds(3)},
                {"kind": "junk", "count": 777},
                _part(show, 30, 12, 30),
            ],
        },
        {
            "name": "re-encoded copy",
            "track": [_episode(show, 20)],
            "other": [_episode(0x33330009, 20)],
        },
    ]


def digest_checks() -> List[Dict[str, Any]]:
    """Known answers for the three digest functions."""
    out = []
    for label, data in (
        ("empty", b""),
        ("abc", b"abc"),
        ("frame", xorshift_frames(0x1234ABCD, 1)),
        ("long", xorshift_frames(0x00C0FFEE, 20)),  # crosses Adler-32's modulo block
    ):
        out.append(
            {
                "label": label,
                "data": base64.b64encode(data).decode("ascii"),
                "crc32": zlib.crc32(data),
                "adler32": zlib.adler32(data),
            }
        )
    return out


def window_checks() -> List[Dict[str, Any]]:
    pairs = [(0, 1), (0xFFFFFFFF, 0xFFFFFFFF), (0x12345678, 0x9ABCDEF0), (7, 0x80000000)]
    return [{"pairs": [list(p) for p in pairs], "digest": window_digest(pairs)}]


def build_vectors() -> Dict[str, Any]:
    """Everything the fixture file holds."""
    out_cases = []
    for case in cases():
        track_bytes = render(case["track"])
        other_bytes = render(case["other"])
        track = build_sync_track(_chunks(track_bytes, 7919), "vectors")
        result = align(track, _chunks(other_bytes, 4099))
        out_cases.append(
            {
                "name": case["name"],
                "track": case["track"],
                "other": case["other"],
                "track_stream_bytes": len(track_bytes),
                "track_stream_crc32": zlib.crc32(track_bytes),
                "other_stream_bytes": len(other_bytes),
                "other_stream_crc32": zlib.crc32(other_bytes),
                "sync_track": base64.b64encode(track.to_bytes()).decode("ascii"),
                "expected": {
                    "anchors_total": result.anchors_total,
                    "anchors_hit": result.anchors_hit,
                    "anchors_inlier": result.anchors_inlier,
                    "other_bytes": result.other_bytes,
                    "other_duration_s": result.other_duration_s,
                    "track_duration_s": result.track_duration_s,
                    "coverage": result.coverage,
                    "spans": [
                        {
                            "src_start_s": span.src_start_s,
                            "src_end_s": span.src_end_s,
                            "offset_s": span.offset_s,
                            "anchors": span.anchors,
                        }
                        for span in result.spans
                    ],
                },
            }
        )
    return {
        "comment": "Generated by tests/unit/core/sync_track_vectors.py. Do not edit by hand.",
        "digests": digest_checks(),
        "windows": window_checks(),
        "cases": out_cases,
    }
