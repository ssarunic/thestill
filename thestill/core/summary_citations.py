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

"""Summary citation resolution (spec #54).

The summarizer emits human-readable timestamp labels. This module resolves
those labels against the annotated transcript once, stores a durable sidecar,
and rewrites the summary markdown so the web UI can render clickable chips.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from structlog import get_logger

from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, WordSpan
from thestill.models.podcast import Episode
from thestill.utils.file_storage import FileStorage
from thestill.utils.path_manager import PathManager

logger = get_logger(__name__)

SCHEMA_VERSION = 1
DEFAULT_GAP_TOLERANCE_SECONDS = 5.0
DEFAULT_MAX_CITATIONS = 500

_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_RANGE_RE = re.compile(
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b\s*[-\u2013\u2014]\s*(?:\b\d{1,2}:\d{2}(?::\d{2})?\b|End\b)",
    re.IGNORECASE,
)
# The trailing ``- TS2`` / ``- End`` of a range, matched right after a citation
# link's destination when healing the legacy split-range form.
_RANGE_TAIL_RE = re.compile(
    r"\s*[-\u2013\u2014]\s*(?:\d{1,2}:\d{2}(?::\d{2})?|End\b)",
    re.IGNORECASE,
)


class _ResolveLabel(Protocol):
    """Resolve a timestamp label to a citation link, or ``None`` if unresolved.

    ``display`` overrides the visible link text without changing the seek
    target or recorded ``raw_label``. It is used for ranges, where the whole
    ``TS1 - TS2`` span becomes one clickable label that seeks to ``TS1``.
    """

    def __call__(
        self,
        label: str,
        *,
        display: Optional[str] = None,
        is_range: bool = False,
        range_end: Optional[str] = None,
    ) -> Optional[str]: ...


class SummaryCitation(BaseModel):
    """One timestamp citation resolved from summary markdown."""

    id: str
    raw_label: str
    cited_playback_s: Optional[float] = None
    target_playback_s: Optional[float] = None
    segment_id_hint: Optional[int] = None
    source_segment_ids: List[int] = Field(default_factory=list)
    source_word_span: Optional[WordSpan] = None
    resolved: bool = False


class SummaryCitationsSidecar(BaseModel):
    """Sidecar persisted next to a summary markdown file."""

    schema_version: int = SCHEMA_VERSION
    episode_id: str
    summary_sha256: str
    clean_transcript_json_path: Optional[str] = None
    transcript_algorithm_version: str
    playback_time_offset_seconds: float
    citations: List[SummaryCitation] = Field(default_factory=list)


@dataclass(frozen=True)
class CitationResolution:
    """Result of resolving citations in one summary."""

    markdown: str
    sidecar: SummaryCitationsSidecar
    resolved_count: int
    unresolved_count: int


@dataclass(frozen=True)
class PersistedSummaryCitations:
    """Outcome from writing a summary and optional citations sidecar."""

    markdown: str
    sidecar: Optional[SummaryCitationsSidecar]
    sidecar_key: Optional[str]
    resolved_count: int
    unresolved_count: int


@dataclass(frozen=True)
class BackfillCitationResult:
    """Outcome from a backfill attempt for one episode."""

    episode_id: str
    summary_key: str
    sidecar_key: str
    changed: bool
    written: bool
    skipped: bool
    reason: str
    resolved_count: int = 0
    unresolved_count: int = 0


def summary_sha256(markdown: str) -> str:
    """Return the stable hash stored in citation sidecars."""

    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def summary_citations_key(summary_key: str) -> str:
    """Return the FileStorage key for a summary citations sidecar."""

    if summary_key.endswith(".md"):
        return f"{summary_key[:-3]}.citations.json"
    return f"{summary_key}.citations.json"


def summary_citations_path(summary_path: Path) -> Path:
    """Return the local-style path for a summary citations sidecar."""

    return summary_path.with_suffix(".citations.json")


def parse_timestamp_label(label: str) -> Optional[float]:
    """Parse ``MM:SS`` or ``HH:MM:SS`` into seconds."""

    parts = label.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        if not (minutes.isdigit() and seconds.isdigit()):
            return None
        ss = int(seconds)
        if ss >= 60:
            return None
        return float(int(minutes) * 60 + ss)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        if not (hours.isdigit() and minutes.isdigit() and seconds.isdigit()):
            return None
        mm = int(minutes)
        ss = int(seconds)
        if mm >= 60 or ss >= 60:
            return None
        return float(int(hours) * 3600 + mm * 60 + ss)
    return None


def format_deep_link_seconds(seconds: float) -> str:
    """Format seconds for ``?t=`` links without noisy trailing decimals."""

    if seconds.is_integer():
        return str(int(seconds))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def resolve_summary_citations(
    markdown: str,
    transcript: AnnotatedTranscript,
    *,
    episode_id: Optional[str] = None,
    clean_transcript_json_path: Optional[str] = None,
    gap_tolerance_seconds: float = DEFAULT_GAP_TOLERANCE_SECONDS,
    max_citations: int = DEFAULT_MAX_CITATIONS,
) -> CitationResolution:
    """Resolve timestamp citations in ``markdown`` against ``transcript``.

    The emitted timestamps are playback-time labels because the summarizer reads
    ``AnnotatedTranscript.to_blended_markdown()`` output. Segment ``start`` /
    ``end`` values are raw transcript time, so resolution subtracts
    ``playback_time_offset_seconds`` before looking up a segment.
    """

    builder = _CitationRewriteBuilder(
        transcript=transcript,
        gap_tolerance_seconds=gap_tolerance_seconds,
        max_citations=max_citations,
    )
    rewritten = _rewrite_markdown_citation_groups(markdown, builder.resolve_label)
    sidecar = SummaryCitationsSidecar(
        episode_id=episode_id or transcript.episode_id,
        summary_sha256=summary_sha256(rewritten),
        clean_transcript_json_path=clean_transcript_json_path,
        transcript_algorithm_version=transcript.algorithm_version,
        playback_time_offset_seconds=transcript.playback_time_offset_seconds,
        citations=builder.citations,
    )
    return CitationResolution(
        markdown=rewritten,
        sidecar=sidecar,
        resolved_count=sum(1 for c in builder.citations if c.resolved),
        unresolved_count=sum(1 for c in builder.citations if not c.resolved),
    )


def resolve_and_persist_summary_citations(
    *,
    summary_markdown: str,
    episode: Episode,
    summary_path: Path,
    path_manager: PathManager,
    file_storage: FileStorage,
) -> PersistedSummaryCitations:
    """Persist a summary and its citations sidecar when an annotated transcript exists."""

    summary_key = path_manager.to_relative(summary_path)
    annotated = _load_annotated_for_episode(
        episode=episode,
        path_manager=path_manager,
        file_storage=file_storage,
    )
    if annotated is None:
        file_storage.write_text(summary_key, summary_markdown)
        return PersistedSummaryCitations(
            markdown=summary_markdown,
            sidecar=None,
            sidecar_key=None,
            resolved_count=0,
            unresolved_count=0,
        )

    result = resolve_summary_citations(
        summary_markdown,
        annotated,
        episode_id=episode.id,
        clean_transcript_json_path=episode.clean_transcript_json_path,
    )
    sidecar_key = summary_citations_key(summary_key)
    file_storage.write_text(summary_key, result.markdown)
    file_storage.write_text(sidecar_key, result.sidecar.model_dump_json(indent=2))
    return PersistedSummaryCitations(
        markdown=result.markdown,
        sidecar=result.sidecar,
        sidecar_key=sidecar_key,
        resolved_count=result.resolved_count,
        unresolved_count=result.unresolved_count,
    )


def backfill_summary_citations_for_episode(
    *,
    episode: Episode,
    path_manager: PathManager,
    file_storage: FileStorage,
    write: bool = False,
    force: bool = False,
) -> BackfillCitationResult:
    """Resolve citations for an existing summary without calling the LLM."""

    if not episode.summary_path:
        return BackfillCitationResult(
            episode_id=episode.id,
            summary_key="",
            sidecar_key="",
            changed=False,
            written=False,
            skipped=True,
            reason="missing_summary_path",
        )
    summary_path = path_manager.summary_file(episode.summary_path)
    summary_key = path_manager.to_relative(summary_path)
    sidecar_key = summary_citations_key(summary_key)

    if not episode.clean_transcript_json_path:
        return BackfillCitationResult(
            episode_id=episode.id,
            summary_key=summary_key,
            sidecar_key=sidecar_key,
            changed=False,
            written=False,
            skipped=True,
            reason="missing_annotated_transcript",
        )

    try:
        summary_markdown = file_storage.read_text(summary_key)
    except FileNotFoundError:
        return BackfillCitationResult(
            episode_id=episode.id,
            summary_key=summary_key,
            sidecar_key=sidecar_key,
            changed=False,
            written=False,
            skipped=True,
            reason="summary_file_missing",
        )

    if not force:
        existing = _read_sidecar(file_storage, sidecar_key)
        if existing is not None and existing.summary_sha256 == summary_sha256(summary_markdown):
            return BackfillCitationResult(
                episode_id=episode.id,
                summary_key=summary_key,
                sidecar_key=sidecar_key,
                changed=False,
                written=False,
                skipped=True,
                reason="already_current",
                resolved_count=sum(1 for c in existing.citations if c.resolved),
                unresolved_count=sum(1 for c in existing.citations if not c.resolved),
            )

    annotated = _load_annotated_for_episode(
        episode=episode,
        path_manager=path_manager,
        file_storage=file_storage,
    )
    if annotated is None:
        return BackfillCitationResult(
            episode_id=episode.id,
            summary_key=summary_key,
            sidecar_key=sidecar_key,
            changed=False,
            written=False,
            skipped=True,
            reason="annotated_transcript_missing",
        )

    resolved = resolve_summary_citations(
        summary_markdown,
        annotated,
        episode_id=episode.id,
        clean_transcript_json_path=episode.clean_transcript_json_path,
    )
    changed = resolved.markdown != summary_markdown
    if write:
        if changed:
            file_storage.write_text(summary_key, resolved.markdown)
        file_storage.write_text(sidecar_key, resolved.sidecar.model_dump_json(indent=2))

    return BackfillCitationResult(
        episode_id=episode.id,
        summary_key=summary_key,
        sidecar_key=sidecar_key,
        changed=changed,
        written=write,
        skipped=False,
        reason="resolved",
        resolved_count=resolved.resolved_count,
        unresolved_count=resolved.unresolved_count,
    )


def load_valid_citations_for_api(
    *,
    summary_markdown: str,
    episode: Episode,
    transcript: AnnotatedTranscript,
    summary_path: Path,
    path_manager: PathManager,
    file_storage: FileStorage,
) -> Optional[List[dict]]:
    """Load and validate a sidecar, returning frontend-safe citation dicts."""

    sidecar_key = summary_citations_key(path_manager.to_relative(summary_path))
    sidecar = _read_sidecar(file_storage, sidecar_key)
    if sidecar is None:
        return None
    if sidecar.schema_version != SCHEMA_VERSION:
        logger.warning("summary_citations.schema_mismatch", episode_id=episode.id, sidecar_key=sidecar_key)
        return None
    if sidecar.episode_id != episode.id:
        logger.warning("summary_citations.episode_mismatch", episode_id=episode.id, sidecar_key=sidecar_key)
        return None
    if sidecar.summary_sha256 != summary_sha256(summary_markdown):
        logger.warning("summary_citations.summary_hash_mismatch", episode_id=episode.id, sidecar_key=sidecar_key)
        return None
    if abs(sidecar.playback_time_offset_seconds - transcript.playback_time_offset_seconds) > 0.001:
        logger.warning("summary_citations.offset_mismatch", episode_id=episode.id, sidecar_key=sidecar_key)
        return None

    out: List[dict] = []
    version_matches = sidecar.transcript_algorithm_version == transcript.algorithm_version
    segment_ids = {segment.id for segment in transcript.segments}
    for citation in sidecar.citations:
        if not citation.resolved:
            continue
        segment_id = citation.segment_id_hint
        if not version_matches or segment_id not in segment_ids:
            segment = resolve_segment_from_source_anchor(citation, transcript)
            if segment is None:
                continue
            segment_id = segment.id
        out.append(
            {
                "id": citation.id,
                "raw_label": citation.raw_label,
                "cited_playback_s": citation.cited_playback_s,
                "target_playback_s": citation.target_playback_s,
                "segment_id_hint": segment_id,
                "source_segment_ids": citation.source_segment_ids,
                "resolved": True,
            }
        )
    return out


def resolve_segment_from_source_anchor(
    citation: SummaryCitation,
    transcript: AnnotatedTranscript,
) -> Optional[AnnotatedSegment]:
    """Resolve a citation's durable raw anchor into the current transcript."""

    wanted = set(citation.source_segment_ids)
    if not wanted:
        return None
    raw_t = None
    if citation.cited_playback_s is not None:
        raw_t = citation.cited_playback_s - transcript.playback_time_offset_seconds

    best: Optional[AnnotatedSegment] = None
    best_score: tuple[int, float] = (0, float("-inf"))
    for segment in transcript.segments:
        overlap = len(wanted.intersection(segment.source_segment_ids))
        if overlap <= 0:
            continue
        if raw_t is None:
            proximity = 0.0
        elif segment.start <= raw_t <= segment.end:
            proximity = 1_000_000.0
        else:
            proximity = -abs(segment.start - raw_t)
        score = (overlap, proximity)
        if score > best_score:
            best = segment
            best_score = score
    return best


class _CitationRewriteBuilder:
    """Mutable helper for one markdown rewrite pass."""

    def __init__(
        self,
        *,
        transcript: AnnotatedTranscript,
        gap_tolerance_seconds: float,
        max_citations: int,
    ) -> None:
        self.transcript = transcript
        self.gap_tolerance_seconds = gap_tolerance_seconds
        self.max_citations = max_citations
        self.citations: List[SummaryCitation] = []
        self._content_segments = sorted(
            [segment for segment in transcript.segments if segment.kind == "content"],
            key=lambda segment: segment.start,
        )

    def resolve_label(
        self,
        label: str,
        *,
        display: Optional[str] = None,
        is_range: bool = False,
        range_end: Optional[str] = None,
    ) -> Optional[str]:
        citation_id = f"c{len(self.citations)}"
        if len(self.citations) >= self.max_citations:
            self.citations.append(SummaryCitation(id=citation_id, raw_label=label, resolved=False))
            return None

        start_s = parse_timestamp_label(label)
        if start_s is None:
            self.citations.append(SummaryCitation(id=citation_id, raw_label=label, resolved=False))
            return None

        if is_range:
            end_s = parse_timestamp_label(range_end) if range_end else self.transcript.transcript_source_duration_s
            segment, seek_s = self._resolve_range_segment(start_s, end_s)
        else:
            segment = self._resolve_segment(start_s)
            seek_s = start_s

        if segment is None or seek_s is None:
            self.citations.append(SummaryCitation(id=citation_id, raw_label=label, resolved=False))
            return None

        citation = SummaryCitation(
            id=citation_id,
            raw_label=label,
            cited_playback_s=seek_s,
            target_playback_s=seek_s,
            segment_id_hint=segment.id,
            source_segment_ids=segment.source_segment_ids,
            source_word_span=segment.source_word_span,
            resolved=True,
        )
        self.citations.append(citation)
        query = urlencode({"t": format_deep_link_seconds(seek_s), "cite": citation_id})
        text = display if display is not None else label
        return f"[{text}](?{query})"

    def _resolve_range_segment(
        self, start_s: float, end_s: Optional[float]
    ) -> tuple[Optional[AnnotatedSegment], Optional[float]]:
        # A range whose start lands on (or near) a content segment seeks to the
        # labeled start, unchanged. When the start falls in a stripped intro/ad
        # — no content segment within tolerance — seek to the first content
        # segment that begins within the chapter span, so a Timeline entry still
        # links and lands on real content instead of failing to resolve.
        direct = self._resolve_segment(start_s)
        if direct is not None:
            return direct, start_s
        offset = self.transcript.playback_time_offset_seconds
        upper = end_s if end_s is not None else float("inf")
        for segment in self._content_segments:
            seg_playback = segment.start + offset
            if start_s <= seg_playback <= upper:
                return segment, seg_playback
        return None, None

    def _resolve_segment(self, playback_s: Optional[float]) -> Optional[AnnotatedSegment]:
        if playback_s is None or playback_s < 0:
            return None
        if not self._content_segments:
            return None

        offset = self.transcript.playback_time_offset_seconds
        duration = self.transcript.transcript_source_duration_s
        if duration is not None and playback_s > duration + offset:
            return None

        raw_t = playback_s - offset
        for segment in self._content_segments:
            if segment.start <= raw_t <= segment.end:
                return segment

        nearest = min(self._content_segments, key=lambda segment: abs(segment.start - raw_t))
        if abs(nearest.start - raw_t) <= self.gap_tolerance_seconds:
            return nearest
        return None


def _rewrite_markdown_citation_groups(
    markdown: str,
    resolve_label: _ResolveLabel,
) -> str:
    """Rewrite bracketed timestamp groups while skipping markdown syntax."""

    out: List[str] = []
    in_fence: Optional[str] = None
    for line in markdown.splitlines(keepends=True):
        fence = _line_fence_marker(line)
        if in_fence is not None:
            out.append(line)
            if fence == in_fence:
                in_fence = None
            continue
        if fence is not None:
            in_fence = fence
            out.append(line)
            continue
        out.append(_rewrite_inline_citation_groups(line, resolve_label))
    return "".join(out)


def _line_fence_marker(line: str) -> Optional[str]:
    stripped = line.lstrip()
    if stripped.startswith("```"):
        return "```"
    if stripped.startswith("~~~"):
        return "~~~"
    return None


def _rewrite_inline_citation_groups(
    text: str,
    resolve_label: _ResolveLabel,
) -> str:
    out: List[str] = []
    i = 0
    while i < len(text):
        if text[i] == "`":
            end = _find_code_span_end(text, i)
            out.append(text[i:end])
            i = end
            continue

        link_start = i
        if text[i] == "!" and i + 1 < len(text) and text[i + 1] == "[":
            link_start = i + 1
        if text[link_start] == "[":
            close = _find_unescaped(text, "]", link_start + 1)
            if close == -1:
                out.append(text[i])
                i += 1
                continue
            inner = text[link_start + 1 : close]
            next_char = text[close + 1] if close + 1 < len(text) else ""
            # Two timestamp brackets written back-to-back (``[00:10][00:45]``)
            # are adjacent citations, not ``[text][ref]`` reference-link
            # syntax. Only honour a following ``[`` as a reference link when
            # the first bracket is not itself a citation candidate — otherwise
            # both timestamps get silently skipped.
            is_citation_candidate = (
                i == link_start and bool(_TIMESTAMP_RE.search(inner)) and not _RANGE_RE.search(inner)
            )
            # Existing inline/reference links/images are not citation text.
            if next_char in ("(", "[") and not (next_char == "[" and is_citation_candidate):
                if i == link_start and next_char == "(":
                    destination_end = _find_link_destination_end(text, close + 2)
                    if destination_end != -1:
                        destination = text[close + 2 : destination_end]
                        if _is_summary_citation_destination(destination):
                            # Heal the legacy split-range form
                            # ``[TS1](cite) - TS2`` -> ``[TS1 - TS2](cite)`` so
                            # a range renders as one link (an earlier backfill
                            # linked only the start and left ``- TS2`` as text).
                            tail_end = _match_range_tail(text, destination_end + 1)
                            if tail_end != -1 and parse_timestamp_label(inner) is not None:
                                merged = resolve_label(
                                    inner,
                                    display=inner + text[destination_end + 1 : tail_end],
                                )
                                if merged is not None:
                                    out.append(merged)
                                    i = tail_end
                                    continue
                            rewritten = _rewrite_bracket_inner(inner, resolve_label)
                            if rewritten is not None:
                                out.append(rewritten)
                                i = destination_end + 1
                                continue
                end = _skip_existing_link(text, i, close)
                out.append(text[i:end])
                i = end
                continue

            rewritten = _rewrite_bracket_inner(inner, resolve_label)
            if rewritten is None:
                out.append(text[i : close + 1])
            else:
                if i < link_start:
                    out.append(text[i:link_start])
                out.append(rewritten)
            i = close + 1
            continue

        out.append(text[i])
        i += 1
    return "".join(out)


def _rewrite_bracket_inner(
    inner: str,
    resolve_label: _ResolveLabel,
) -> Optional[str]:
    if not _TIMESTAMP_RE.search(inner):
        return None
    if _RANGE_RE.search(inner):
        return _rewrite_range_start(inner, resolve_label)

    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        label = match.group(0)
        replacement = resolve_label(label)
        changed = True
        if replacement is None:
            # Unresolved timestamp: keep it bracketed so it still reads as a
            # citation marker. Without this, an unresolved sibling in a
            # multi-timestamp group (``[00:10, 99:59]``) would inherit the
            # group's dropped brackets and render as bare floating text.
            return f"[{label}]"
        return replacement

    rewritten = _TIMESTAMP_RE.sub(replace, inner)
    return rewritten if changed else None


def _rewrite_range_start(
    inner: str,
    resolve_label: _ResolveLabel,
) -> Optional[str]:
    """Link a whole range group (``[00:00 - 12:06]``) as one seek to its start.

    A range is a span, not a point, so the entire ``TS1 - TS2`` text becomes a
    single clickable label that seeks to ``TS1`` — rather than styling ``TS1``
    as a link and leaving ``TS2`` as plain text, which reads inconsistently.
    Timeline chapter markers use this form. If the start does not resolve, the
    whole range is left untouched.
    """

    labels = _TIMESTAMP_RE.findall(inner)
    if not labels:
        return None
    start_label = labels[0]
    end_label = labels[1] if len(labels) > 1 else None
    return resolve_label(start_label, display=inner, is_range=True, range_end=end_label)


def _find_code_span_end(text: str, start: int) -> int:
    tick_count = 1
    while start + tick_count < len(text) and text[start + tick_count] == "`":
        tick_count += 1
    marker = "`" * tick_count
    end = text.find(marker, start + tick_count)
    return len(text) if end == -1 else end + tick_count


def _find_unescaped(text: str, needle: str, start: int) -> int:
    idx = start
    while True:
        idx = text.find(needle, idx)
        if idx == -1:
            return -1
        if idx == 0 or text[idx - 1] != "\\":
            return idx
        idx += 1


def _skip_existing_link(text: str, start: int, close_bracket: int) -> int:
    if close_bracket + 1 >= len(text):
        return close_bracket + 1
    if text[close_bracket + 1] == "[":
        close = _find_unescaped(text, "]", close_bracket + 2)
        return len(text) if close == -1 else close + 1
    if text[close_bracket + 1] == "(":
        close = _find_link_destination_end(text, close_bracket + 2)
        return len(text) if close == -1 else close + 1
    return close_bracket + 1


def _find_link_destination_end(text: str, start: int) -> int:
    depth = 1
    idx = start
    while idx < len(text):
        char = text[idx]
        if char == "\\":
            idx += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return -1


def _is_summary_citation_destination(destination: str) -> bool:
    """Return true for citation links previously emitted by this resolver."""

    return destination.startswith("?") and "cite=" in destination


def _match_range_tail(text: str, start: int) -> int:
    """Return the index after a ``- TS2`` / ``- End`` range tail at ``start``, or -1."""

    match = _RANGE_TAIL_RE.match(text, start)
    return match.end() if match else -1


def _load_annotated_for_episode(
    *,
    episode: Episode,
    path_manager: PathManager,
    file_storage: FileStorage,
) -> Optional[AnnotatedTranscript]:
    if not episode.clean_transcript_json_path:
        return None
    sidecar_path = path_manager.clean_transcript_file(episode.clean_transcript_json_path)
    try:
        annotated = AnnotatedTranscript.model_validate_json(
            file_storage.read_text(path_manager.to_relative(sidecar_path))
        )
    except FileNotFoundError:
        logger.warning("summary_citations.annotated_missing", episode_id=episode.id)
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("summary_citations.annotated_invalid", episode_id=episode.id, error=str(exc))
        return None
    annotated.playback_time_offset_seconds = episode.playback_time_offset_seconds
    return annotated


def _read_sidecar(file_storage: FileStorage, sidecar_key: str) -> Optional[SummaryCitationsSidecar]:
    try:
        return SummaryCitationsSidecar.model_validate_json(file_storage.read_text(sidecar_key))
    except FileNotFoundError:
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("summary_citations.sidecar_invalid", sidecar_key=sidecar_key, error=str(exc))
        return None
