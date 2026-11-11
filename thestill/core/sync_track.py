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

"""Sync tracks (spec #116): landmarks of the transcribed copy of an episode.

Publishers splice ads into an MP3 without re-encoding the episode, so the
episode's own compressed frames are byte-identical in every download. A sync
track keeps one landmark per second of the copy that was transcribed: the
time of a run of four frames and a short digest of their bytes. Finding the
same digest in another copy says how far that copy is shifted at that point.

Nothing here decodes audio. The parser reads MPEG audio frame headers and
hands out each frame's bytes.

The digest must be reproducible in the browser (Phase 2), so it uses only
CRC-32 and Adler-32:

- frame digest: ``crc32(frame)`` and ``adler32(frame)``, both unsigned 32 bit;
- window digest over four consecutive frames: pack the eight values
  little-endian (crc, adler per frame, in order), then
  ``crc32(packed) << 16 | (adler32(packed) & 0xFFFF)`` — 48 bits.
"""

from __future__ import annotations

import bisect
import statistics
import struct
import zlib
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Tuple

ALGORITHM_VERSION = 1
MAGIC = b"TSYN"
CODEC_MP3 = 1

#: Frames per landmark. Four frames are about 104 ms at 44.1 kHz, so a single
#: silent frame cannot match on its own.
WINDOW_FRAMES = 4
#: One landmark per this many seconds of audio.
ANCHOR_INTERVAL_S = 1.0
#: A run of this many consecutive landmarks agreeing on one offset is needed
#: before the offset is trusted (single hits false-match on silence and
#: repeated ads; see the spec's Background findings).
MIN_RUN = 5
#: A run may skip at most this many of the track's landmarks in a row. More
#: unmatched landmarks than that means the audio between two hits is not the
#: tracked audio (an ad swapped for another of the same length keeps the
#: offset on both sides), so the run ends there.
MAX_MISSED_IN_RUN = 2
#: Two offsets are "the same" within this many seconds. Anchor times are
#: stored in milliseconds, so rounding alone can differ by one.
OFFSET_TOLERANCE_S = 0.02

_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_MAX_FRAME_BYTES = 1441 + 1  # MPEG-1 Layer III, 320 kbps at 32 kHz, padded


class SyncTrackError(ValueError):
    """The bytes are not a usable sync track or not parseable MP3."""


@dataclass(frozen=True)
class Frame:
    """One MPEG audio frame: where it starts in time and its raw bytes."""

    start_s: float
    data: bytes


def _frame_header(buf: bytearray, i: int) -> Optional[Tuple[int, int, int]]:
    """Parse a Layer III frame header at ``buf[i:i+4]``.

    Returns ``(frame_bytes, samples, sample_rate)`` or ``None`` when the four
    bytes are not a valid header. Free-format streams (bitrate index 0) are
    not supported.
    """
    if buf[i] != 0xFF or (buf[i + 1] & 0xE0) != 0xE0:
        return None
    version = (buf[i + 1] >> 3) & 0x03
    layer = (buf[i + 1] >> 1) & 0x03
    if version == 1 or layer != 1:
        return None
    bitrate_index = buf[i + 2] >> 4
    rate_index = (buf[i + 2] >> 2) & 0x03
    if bitrate_index in (0, 15) or rate_index == 3:
        return None
    sample_rate = _SAMPLE_RATES[version][rate_index]
    padding = (buf[i + 2] >> 1) & 0x01
    if version == 3:
        bitrate = _BITRATES_V1_L3[bitrate_index] * 1000
        return (144 * bitrate // sample_rate + padding, 1152, sample_rate)
    bitrate = _BITRATES_V2_L3[bitrate_index] * 1000
    return (72 * bitrate // sample_rate + padding, 576, sample_rate)


def iter_mp3_frames(chunks: Iterable[bytes]) -> Iterator[Frame]:
    """Yield the MPEG Layer III frames of a byte stream, in order.

    ID3v2 tags (at the start or between spliced segments) are skipped by
    their declared size. Anything else that is not a frame is skipped one
    byte at a time. After any skip, a header is accepted only when the frame
    it describes is followed by another valid header, which keeps random
    ``0xFF`` bytes in tags or artwork from being read as frames.
    """
    buf = bytearray()
    pos = 0
    time_s = 0.0
    in_sync = False
    eof = False
    source = iter(chunks)

    def fill(need: int) -> bool:
        """Make ``buf[pos:pos+need]`` available; False at end of stream."""
        nonlocal buf, pos, eof
        while len(buf) - pos < need and not eof:
            if pos > 1 << 16:
                del buf[:pos]
                pos = 0
            try:
                buf += next(source)
            except StopIteration:
                eof = True
        return len(buf) - pos >= need

    while fill(4):
        if buf[pos : pos + 3] == b"ID3" and fill(10):
            size = (
                (buf[pos + 6] & 0x7F) << 21
                | (buf[pos + 7] & 0x7F) << 14
                | (buf[pos + 8] & 0x7F) << 7
                | (buf[pos + 9] & 0x7F)
            )
            skip = 10 + size
            while skip > 0 and fill(1):
                step = min(skip, len(buf) - pos)
                pos += step
                skip -= step
            in_sync = False
            continue
        header = _frame_header(buf, pos)
        if header is None:
            pos += 1
            in_sync = False
            continue
        length, samples, sample_rate = header
        if not in_sync:
            # Confirm with the next header before trusting a resync. At the
            # very end of the stream there is no next frame to check.
            if fill(length + 4):
                if _frame_header(buf, pos + length) is None:
                    pos += 1
                    continue
            elif not fill(length):
                break
        if not fill(length):
            break
        yield Frame(start_s=time_s, data=bytes(buf[pos : pos + length]))
        time_s += samples / sample_rate
        pos += length
        in_sync = True


def window_digest(frame_digests: Iterable[Tuple[int, int]]) -> int:
    """48-bit digest of ``WINDOW_FRAMES`` consecutive frame digests."""
    flat: List[int] = []
    for crc, adler in frame_digests:
        flat.append(crc)
        flat.append(adler)
    packed = struct.pack(f"<{len(flat)}I", *flat)
    return (zlib.crc32(packed) << 16) | (zlib.adler32(packed) & 0xFFFF)


def _iter_windows(chunks: Iterable[bytes]) -> Iterator[Tuple[float, int, int]]:
    """Yield ``(window_start_s, digest, frame_bytes)`` for every frame.

    ``digest`` is ``-1`` until ``WINDOW_FRAMES`` frames have been seen; the
    window is the run of frames *ending* at the current one, and
    ``window_start_s`` is the start time of its first frame.
    """
    recent: deque = deque(maxlen=WINDOW_FRAMES)
    for frame in iter_mp3_frames(chunks):
        recent.append((frame.start_s, (zlib.crc32(frame.data), zlib.adler32(frame.data))))
        if len(recent) == WINDOW_FRAMES:
            yield (recent[0][0], window_digest(d for _, d in recent), len(frame.data))
        else:
            yield (recent[0][0], -1, len(frame.data))


@dataclass(frozen=True)
class Anchor:
    """A landmark: when a window starts in the transcribed copy, and its digest."""

    time_ms: int
    digest: int


@dataclass(frozen=True)
class BaseSpan:
    """Transcript time ``[src_start_s, src_end_s)`` sits ``offset_s`` later in
    the copy the anchors were cut from (Phase 5; empty for a track cut from
    the transcribed copy itself)."""

    src_start_s: float
    src_end_s: float
    offset_s: float


@dataclass
class SyncTrack:
    """The stored artefact. ``generation`` ties it to one raw transcript."""

    generation: str
    sample_rate: int
    frame_count: int
    audio_bytes: int
    duration_s: float
    anchors: List[Anchor]
    base_spans: List[BaseSpan] = field(default_factory=list)
    codec: int = CODEC_MP3
    version: int = ALGORITHM_VERSION
    #: Build statistics; not serialized.
    anchors_dropped_duplicate: int = 0

    def to_bytes(self) -> bytes:
        generation = self.generation.encode("utf-8")
        if len(generation) > 255:
            raise SyncTrackError("generation is too long")
        out = bytearray()
        out += MAGIC
        out += struct.pack(
            "<BBIIQdB",
            self.version,
            self.codec,
            self.sample_rate,
            self.frame_count,
            self.audio_bytes,
            self.duration_s,
            len(generation),
        )
        out += generation
        out += struct.pack("<H", len(self.base_spans))
        for span in self.base_spans:
            out += struct.pack("<ddd", span.src_start_s, span.src_end_s, span.offset_s)
        out += struct.pack("<I", len(self.anchors))
        for anchor in self.anchors:
            out += struct.pack("<IIH", anchor.time_ms, anchor.digest >> 16, anchor.digest & 0xFFFF)
        return bytes(out)

    @classmethod
    def from_bytes(cls, data: bytes) -> "SyncTrack":
        try:
            if data[:4] != MAGIC:
                raise SyncTrackError("not a sync track")
            head = struct.Struct("<BBIIQdB")
            version, codec, sample_rate, frame_count, audio_bytes, duration_s, gen_len = head.unpack_from(data, 4)
            if version != ALGORITHM_VERSION:
                raise SyncTrackError(f"unsupported sync track version {version}")
            pos = 4 + head.size
            generation = data[pos : pos + gen_len].decode("utf-8")
            pos += gen_len
            (span_count,) = struct.unpack_from("<H", data, pos)
            pos += 2
            base_spans = []
            for _ in range(span_count):
                base_spans.append(BaseSpan(*struct.unpack_from("<ddd", data, pos)))
                pos += 24
            (anchor_count,) = struct.unpack_from("<I", data, pos)
            pos += 4
            anchors = []
            for _ in range(anchor_count):
                time_ms, high, low = struct.unpack_from("<IIH", data, pos)
                anchors.append(Anchor(time_ms=time_ms, digest=(high << 16) | low))
                pos += 10
        except (struct.error, UnicodeDecodeError) as exc:
            raise SyncTrackError("truncated or corrupt sync track") from exc
        return cls(
            generation=generation,
            sample_rate=sample_rate,
            frame_count=frame_count,
            audio_bytes=audio_bytes,
            duration_s=duration_s,
            anchors=anchors,
            base_spans=base_spans,
            codec=codec,
            version=version,
        )

    @property
    def unique_ratio(self) -> float:
        """Share of whole seconds that got a landmark."""
        seconds = max(1, int(self.duration_s / ANCHOR_INTERVAL_S))
        return min(1.0, len(self.anchors) / seconds)


def build_sync_track(chunks: Iterable[bytes], generation: str) -> SyncTrack:
    """Build the sync track of one MP3 byte stream.

    For every whole second it takes the first window starting at or after
    that second whose digest occurs exactly once in the whole stream.
    Digests that repeat (silence, a sting played twice) are useless as
    landmarks and are dropped; a second with no unique window inside it gets
    no anchor.

    Raises:
        SyncTrackError: no MPEG Layer III frames were found.
    """
    times: List[float] = []
    digests: List[int] = []
    frame_count = 0
    duration_s = 0.0
    sample_rate = 0

    counted = _CountingChunks(chunks)
    reader = iter_mp3_frames(counted)
    recent: deque = deque(maxlen=WINDOW_FRAMES)
    for frame in reader:
        frame_count += 1
        if not sample_rate:
            sample_rate = _sample_rate_of(frame.data)
        duration_s = frame.start_s
        recent.append((frame.start_s, (zlib.crc32(frame.data), zlib.adler32(frame.data))))
        if len(recent) == WINDOW_FRAMES:
            times.append(recent[0][0])
            digests.append(window_digest(d for _, d in recent))
        last_frame = frame
    if frame_count == 0:
        raise SyncTrackError("no MPEG Layer III frames found")
    duration_s += _frame_duration(last_frame.data)

    occurrences = Counter(digests)
    anchors: List[Anchor] = []
    dropped = 0
    i = 0
    n = len(times)
    second = 0.0
    while i < n:
        while i < n and times[i] < second:
            i += 1
        limit = second + ANCHOR_INTERVAL_S
        j = i
        chosen = None
        while j < n and times[j] < limit:
            if occurrences[digests[j]] == 1:
                chosen = j
                break
            j += 1
        if chosen is not None:
            anchors.append(Anchor(time_ms=int(round(times[chosen] * 1000)), digest=digests[chosen]))
        elif i < n and times[i] < limit:
            dropped += 1
        second = limit

    return SyncTrack(
        generation=generation,
        sample_rate=sample_rate,
        frame_count=frame_count,
        audio_bytes=counted.total,
        duration_s=duration_s,
        anchors=anchors,
        anchors_dropped_duplicate=dropped,
    )


class _CountingChunks:
    """Iterate chunks while counting the bytes that passed through."""

    def __init__(self, chunks: Iterable[bytes]):
        self._chunks = chunks
        self.total = 0

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            self.total += len(chunk)
            yield chunk


def _sample_rate_of(frame: bytes) -> int:
    header = _frame_header(bytearray(frame[:4]), 0)
    return header[2] if header else 0


def _frame_duration(frame: bytes) -> float:
    header = _frame_header(bytearray(frame[:4]), 0)
    return header[1] / header[2] if header else 0.0


@dataclass(frozen=True)
class Span:
    """Source time ``[src_start_s, src_end_s]`` is found ``offset_s`` later in
    the other copy (negative when that copy is shorter up to this point)."""

    src_start_s: float
    src_end_s: float
    offset_s: float
    anchors: int


@dataclass
class Alignment:
    """How one copy of an episode maps onto the copy a sync track describes."""

    spans: List[Span]
    anchors_total: int
    anchors_hit: int
    anchors_inlier: int
    other_duration_s: float
    other_bytes: int
    track_duration_s: float

    @property
    def anchors_outlier(self) -> int:
        return self.anchors_hit - self.anchors_inlier

    @property
    def coverage(self) -> float:
        """Share of the track's duration that falls inside a span."""
        if self.track_duration_s <= 0:
            return 0.0
        covered = sum(s.src_end_s - s.src_start_s for s in self.spans)
        return min(1.0, covered / self.track_duration_s)

    def to_other(self, src_time_s: float) -> Optional[float]:
        """Map a time in the tracked copy to the other copy.

        Returns ``None`` when the time falls in no span (an ad in the
        tracked copy, or a stretch that could not be matched).
        """
        for span in self.spans:
            if span.src_start_s <= src_time_s <= span.src_end_s:
                return src_time_s + span.offset_s
        return None


def align(track: SyncTrack, chunks: Iterable[bytes], *, min_run: int = MIN_RUN) -> Alignment:
    """Locate a sync track's landmarks in another copy and fit offset spans.

    A span is a run of at least ``min_run`` landmarks, consecutive in the
    order they are found in the other copy, in increasing source order, all
    agreeing on one offset, with no more than ``MAX_MISSED_IN_RUN`` of the
    track's landmarks unmatched between two neighbours. Isolated hits are
    dropped. Where runs overlap
    (the same audio occurs twice in the other copy) the heaviest chain that
    moves forward in both copies wins. Adjacent runs with the same offset
    are merged unless landmarks went unmatched between them.
    """
    wanted = {a.digest: a.time_ms / 1000.0 for a in track.anchors}
    anchor_times = sorted(wanted.values())

    def unbroken(earlier_src: float, later_src: float) -> bool:
        """True when few enough landmarks lie unmatched between two hits."""
        between = bisect.bisect_left(anchor_times, later_src) - bisect.bisect_right(anchor_times, earlier_src)
        return between <= MAX_MISSED_IN_RUN

    hits: List[Tuple[float, float]] = []  # (src_s, other_s), in other-copy order
    other_duration = 0.0  # start of the last window; close enough for a report
    other_bytes = 0
    for start_s, digest, nbytes in _iter_windows(chunks):
        other_bytes += nbytes
        src_s = wanted.get(digest) if digest >= 0 else None
        if src_s is not None:
            hits.append((src_s, start_s))
        other_duration = start_s

    runs: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    for src_s, other_s in hits:
        if current:
            prev_src, prev_other = current[-1]
            same_offset = abs((other_s - src_s) - (prev_other - prev_src)) <= OFFSET_TOLERANCE_S
            if same_offset and src_s > prev_src and unbroken(prev_src, src_s):
                current.append((src_s, other_s))
                continue
            runs.append(current)
        current = [(src_s, other_s)]
    if current:
        runs.append(current)

    # The same audio can sit in the other copy twice (a promo played as a
    # pre-roll and again later), so runs can overlap in source time. Keep the
    # heaviest chain of runs that moves forward in both copies.
    candidates = [run for run in runs if len(run) >= min_run]
    best: List[int] = []
    prev: List[int] = []
    for i, run in enumerate(candidates):
        best.append(len(run))
        prev.append(-1)
        for k in range(i):
            earlier = candidates[k]
            if earlier[-1][0] < run[0][0] and earlier[-1][1] < run[0][1] and best[k] + len(run) > best[i]:
                best[i] = best[k] + len(run)
                prev[i] = k
    chain: List[List[Tuple[float, float]]] = []
    if candidates:
        at = max(range(len(candidates)), key=best.__getitem__)
        while at >= 0:
            chain.append(candidates[at])
            at = prev[at]
        chain.reverse()

    spans: List[Span] = []
    inliers = 0
    for run in chain:
        offset = statistics.median(o - s for s, o in run)
        inliers += len(run)
        if (
            spans
            and abs(spans[-1].offset_s - offset) <= OFFSET_TOLERANCE_S
            and unbroken(spans[-1].src_end_s, run[0][0])
        ):
            last = spans[-1]
            spans[-1] = Span(last.src_start_s, run[-1][0], last.offset_s, last.anchors + len(run))
        else:
            spans.append(Span(run[0][0], run[-1][0], offset, len(run)))

    return Alignment(
        spans=spans,
        anchors_total=len(track.anchors),
        anchors_hit=len(hits),
        anchors_inlier=inliers,
        other_duration_s=other_duration,
        other_bytes=other_bytes,
        track_duration_s=track.duration_s,
    )


def iter_file_chunks(path, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    """Read a local file in chunks."""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk
