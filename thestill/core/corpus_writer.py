"""Render the database into the qmd-search corpus (spec #28 §2.3).

Per-episode pages live at ``data/corpus/episodes/<podcast-slug>/<episode-id>.md``
and follow the contract pinned by the Phase 0.1 spike:

- One Markdown block per ``content`` segment from the cleaned-transcript JSON
- Each block is prefixed by an HTML-comment anchor:
  ``<!-- seg id=N t=START_MS-END_MS spk="Speaker" -->``
- Resolved entity mentions are rendered as Obsidian-style wiki-links
  (``[[person:elon-musk]]``) inline at the start of each segment
- A sidecar ``<episode-id>.segmap.json`` maps line ranges to
  ``(seg_id, start_ms, end_ms)`` so qmd hits resolve back to a
  segment-precise citation in O(log n)

Per-entity pages live at ``data/corpus/{persons,companies,topics}/<slug>.md``.
``product`` entities are NOT rendered (per spec §"Markdown corpus layout").

Idempotent: re-running on the same DB state produces byte-identical files.
The Phase 2.4 ``reindex`` handler shells out to ``qmd update --paths``
on the files that this writer touched.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from ..models.entities import EntityRecord, EntityType
from ..models.podcast import Episode, Podcast
from ..repositories.sqlite_entity_repository import SqliteEntityRepository
from ..utils.path_manager import PathManager

logger = get_logger(__name__)


# Wikidata link template — used in entity-page bodies. Matches the
# convention used by the entity-resolution layer (we already store
# ``wikidata_qid`` on every resolved entity).
_WIKIDATA_URL = "https://www.wikidata.org/wiki/{qid}"

_TYPE_TO_RENDERED_DIR = {
    EntityType.PERSON: "persons",
    EntityType.COMPANY: "companies",
    EntityType.TOPIC: "topics",
}


@dataclass(frozen=True)
class SegmapEntry:
    """One row of the ``<id>.segmap.json`` sidecar.

    Persisted shape matches the contract from spec §"Phase 0.1 spike
    outcome": JSON object with snake-case keys, line numbers 1-indexed
    relative to the rendered file.
    """

    seg_id: int
    line_start: int
    line_end: int
    byte_start: int
    byte_end: int
    start_ms: int
    end_ms: int


@dataclass
class WrittenPaths:
    """Tally of files actually changed during a render run.

    Returned by ``write_episode_page`` / ``write_entity_pages`` so the
    caller (Phase 2.4 ``reindex.py``) can pass the exact list of touched
    paths to ``qmd update --paths`` rather than re-scanning the whole
    corpus directory.
    """

    written: List[Path] = field(default_factory=list)
    skipped_unchanged: List[Path] = field(default_factory=list)


class CorpusWriter:
    """Materialise SQLite + cleaned-transcript JSON into the qmd corpus.

    Caller injects ``PathManager`` and ``SqliteEntityRepository`` so the
    writer is testable against a tmp_path-backed DB. The writer never
    talks to qmd directly — that's ``thestill.core.reindex``.
    """

    def __init__(
        self,
        path_manager: PathManager,
        entity_repository: SqliteEntityRepository,
    ):
        self.path_manager = path_manager
        self.entity_repository = entity_repository

    # ------------------------------------------------------------------
    # Episode pages
    # ------------------------------------------------------------------

    def write_episode_page(
        self,
        *,
        podcast: Podcast,
        episode: Episode,
        transcript: AnnotatedTranscript,
        mention_links: Optional[Dict[int, List[str]]] = None,
    ) -> WrittenPaths:
        """Render one episode page + segmap sidecar.

        ``mention_links`` is an optional pre-computed map from
        ``segment_id`` to a list of ``EntityRecord.id`` values that
        should be wiki-linked at the head of the segment block. When
        omitted, the writer queries the entity repository for resolved
        mentions in this episode and builds the map itself.
        """
        out_md = self.path_manager.corpus_episode_file(podcast.slug, episode.id)
        out_segmap = self.path_manager.corpus_episode_segmap_file(podcast.slug, episode.id)
        out_md.parent.mkdir(parents=True, exist_ok=True)

        if mention_links is None:
            mention_links = self._mention_links_for_episode(episode.id)

        body, segmap = _render_episode(
            podcast=podcast,
            episode=episode,
            transcript=transcript,
            mention_links=mention_links,
        )
        result = WrittenPaths()
        if _write_if_changed(out_md, body):
            result.written.append(out_md)
        else:
            result.skipped_unchanged.append(out_md)
        segmap_json = json.dumps([_segmap_to_dict(e) for e in segmap], indent=2)
        if _write_if_changed(out_segmap, segmap_json + "\n"):
            result.written.append(out_segmap)
        else:
            result.skipped_unchanged.append(out_segmap)
        logger.info(
            "corpus_episode_rendered",
            episode_id=episode.id,
            podcast_slug=podcast.slug,
            segments=len(segmap),
            wrote=len(result.written),
            unchanged=len(result.skipped_unchanged),
        )
        return result

    # ------------------------------------------------------------------
    # Entity pages
    # ------------------------------------------------------------------

    def write_entity_page(self, entity: EntityRecord) -> WrittenPaths:
        """Render one entity (person/company/topic) page.

        ``product`` entities are silently skipped — we don't surface
        them as Obsidian-browsable pages in v1.
        """
        result = WrittenPaths()
        if entity.type not in _TYPE_TO_RENDERED_DIR:
            return result

        out_md = self.path_manager.corpus_entity_file(entity.type.value, _slug_from_id(entity.id))
        out_md.parent.mkdir(parents=True, exist_ok=True)

        summary = self.entity_repository.get_entity_summary(entity.id)
        body = _render_entity_page(entity=entity, summary=summary)
        if _write_if_changed(out_md, body):
            result.written.append(out_md)
        else:
            result.skipped_unchanged.append(out_md)
        logger.info(
            "corpus_entity_rendered",
            entity_id=entity.id,
            wrote=len(result.written),
            unchanged=len(result.skipped_unchanged),
        )
        return result

    def write_all_entity_pages(self) -> WrittenPaths:
        """Render every person/company/topic entity in the database.

        Used by ``thestill corpus bootstrap`` for first-run + by the
        Phase 2.4 reindex handler when an episode's resolution
        introduces new entities. Idempotent.
        """
        result = WrittenPaths()
        for entity_type in (EntityType.PERSON, EntityType.COMPANY, EntityType.TOPIC):
            for entity in self.entity_repository.list_entities_by_type(entity_type.value):
                merged = self.write_entity_page(entity)
                result.written.extend(merged.written)
                result.skipped_unchanged.extend(merged.skipped_unchanged)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mention_links_for_episode(self, episode_id: str) -> Dict[int, List[str]]:
        """Build ``segment_id → [entity_id, ...]`` from resolved mentions.

        Only ``person`` / ``company`` / ``topic`` entities get a
        wiki-link in the rendered body — products are not browsable
        pages, so a wiki-link to them would dangle.
        """
        result: Dict[int, List[str]] = {}
        # Single batched query — returns up to ``limit`` resolved
        # mentions for this episode joined with episode/podcast/entity.
        contexts = self.entity_repository.find_mentions(episode_id=episode_id, limit=10_000)
        for ctx in contexts:
            entity_type = ctx.entity_type
            if entity_type not in {"person", "company", "topic"}:
                continue
            seg_id = ctx.mention.segment_id
            entity_id = ctx.mention.entity_id
            if entity_id is None:
                continue
            bucket = result.setdefault(seg_id, [])
            if entity_id not in bucket:
                bucket.append(entity_id)
        return result


# ----------------------------------------------------------------------
# Rendering — pure functions (testable without filesystem or DB)
# ----------------------------------------------------------------------


def _render_episode(
    *,
    podcast: Podcast,
    episode: Episode,
    transcript: AnnotatedTranscript,
    mention_links: Dict[int, List[str]],
) -> tuple[str, List[SegmapEntry]]:
    """Build the rendered Markdown + segmap entries for one episode.

    Returns ``(markdown_text, segmap)``. The caller writes both to
    disk; this function is pure for snapshot-testability.
    """
    lines: List[str] = []
    segmap: List[SegmapEntry] = []

    # --- Frontmatter ---
    fm_lines = _episode_frontmatter(podcast=podcast, episode=episode, transcript=transcript)
    lines.extend(fm_lines)
    lines.append("")  # blank line after frontmatter
    lines.append(f"# {episode.title}")
    lines.append("")

    # --- Content segments ---
    content_segments = [s for s in transcript.segments if s.kind == "content" and s.text.strip()]
    if not content_segments:
        # No body — still emit the frontmatter; segmap is empty.
        return "\n".join(lines) + "\n", segmap

    # Build the rendered file segment by segment. Each segment writes
    # one anchor line + optional wiki-link line + text line + a blank
    # separator. We track the byte cursor as we go so the segmap entries
    # are byte-accurate against the final file.
    rendered_so_far = "\n".join(lines) + "\n"
    byte_cursor = _utf8_size(rendered_so_far)
    line_cursor = len(lines) + 1  # 1-indexed; next segment starts here

    for segment in content_segments:
        block_lines: List[str] = [_segment_anchor(segment)]

        links = mention_links.get(segment.id, [])
        if links:
            block_lines.append(" ".join(f"[[{eid}]]" for eid in links))

        text_line = segment.text.strip()
        if segment.speaker:
            text_line = f"**{segment.speaker}:** {text_line}"
        block_lines.append(text_line)

        # Block as written to disk: lines joined by "\n", then a final
        # "\n" terminator + one blank-line separator before the next
        # block. The blank separator is what makes re-runs round-trip
        # cleanly through Markdown formatters.
        block_text = "\n".join(block_lines) + "\n\n"
        block_size = _utf8_size(block_text)

        line_start = line_cursor
        # Visible content lines: each entry in block_lines is one line.
        # The trailing "\n\n" yields a blank separator AFTER line_end.
        line_end = line_start + len(block_lines) - 1

        segmap.append(
            SegmapEntry(
                seg_id=segment.id,
                line_start=line_start,
                line_end=line_end,
                byte_start=byte_cursor,
                byte_end=byte_cursor + block_size,
                start_ms=int(round(segment.start * 1000)),
                end_ms=int(round(segment.end * 1000)),
            )
        )

        # Advance cursors: ``len(block_lines) + 1`` lines were consumed
        # (content lines + the blank separator).
        rendered_so_far += block_text
        byte_cursor += block_size
        line_cursor += len(block_lines) + 1

    # Trim the final trailing blank that the last block appended so the
    # file ends with a single newline (POSIX convention).
    while rendered_so_far.endswith("\n\n"):
        rendered_so_far = rendered_so_far[:-1]

    return rendered_so_far, segmap


def _segment_anchor(segment: AnnotatedSegment) -> str:
    """``<!-- seg id=42 t=2347000-2389000 spk="Scott Galloway" -->`` form."""
    start_ms = int(round(segment.start * 1000))
    end_ms = int(round(segment.end * 1000))
    speaker_str = ""
    if segment.speaker:
        # HTML-comment-safe speaker quoting: replace inner quotes,
        # collapse whitespace. Anchors are HTML comments so they never
        # render in any Markdown viewer; the only consumer is
        # ``qmd_client.py``'s line resolution.
        cleaned = re.sub(r"\s+", " ", segment.speaker).replace('"', "'")
        speaker_str = f' spk="{cleaned}"'
    return f"<!-- seg id={segment.id} t={start_ms}-{end_ms}{speaker_str} -->"


def _episode_frontmatter(
    *,
    podcast: Podcast,
    episode: Episode,
    transcript: AnnotatedTranscript,
) -> List[str]:
    """YAML frontmatter as per spec §"Markdown corpus layout"."""
    # Duration is derived from the last content segment's end time —
    # the transcript model itself doesn't carry a duration field.
    last_end = max(
        (s.end for s in transcript.segments if s.kind == "content"),
        default=0.0,
    )
    duration_ms = int(round(last_end * 1000))
    pub_date = ""
    if episode.pub_date:
        pub_date = episode.pub_date.date().isoformat() if hasattr(episode.pub_date, "date") else str(episode.pub_date)

    lines: List[str] = ["---"]
    lines.append("type: episode")
    lines.append(f"episode_id: {episode.id}")
    lines.append(f"podcast_id: {podcast.id}")
    lines.append(f"podcast: {_yaml_string(podcast.title)}")
    lines.append(f"title: {_yaml_string(episode.title)}")
    if pub_date:
        lines.append(f"published_at: {pub_date}")
    if duration_ms:
        lines.append(f"duration_ms: {duration_ms}")
    lines.append("language: en")
    lines.append("---")
    return lines


def _render_entity_page(*, entity: EntityRecord, summary: Optional[dict]) -> str:
    """Single-entity Markdown page.

    Frontmatter mirrors the contract from spec §"Person/company/topic
    frontmatter". Body lists notable cooccurring entities + recent
    quotes when the summary blob is populated.
    """
    lines: List[str] = ["---"]
    lines.append(f"type: {entity.type.value}")
    lines.append(f"id: {entity.id}")
    lines.append(f"canonical_name: {_yaml_string(entity.canonical_name)}")
    if entity.wikidata_qid:
        lines.append(f"wikidata_qid: {entity.wikidata_qid}")
    if entity.aliases:
        # YAML inline list — short and unambiguous.
        quoted = ", ".join(_yaml_string(a) for a in entity.aliases)
        lines.append(f"aliases: [{quoted}]")
    if summary and summary.get("mention_count") is not None:
        lines.append(f"mention_count: {summary['mention_count']}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {entity.canonical_name}")
    lines.append("")

    if entity.wikidata_qid:
        lines.append(f"Wikidata: [{entity.wikidata_qid}]({_WIKIDATA_URL.format(qid=entity.wikidata_qid)})")
        lines.append("")

    if entity.description:
        lines.append(entity.description.strip())
        lines.append("")

    if summary:
        cooccurring = summary.get("cooccurring") or []
        if cooccurring:
            lines.append("## Often discussed alongside")
            lines.append("")
            for row in cooccurring[:10]:
                other: EntityRecord = row["entity"]
                lines.append(f"- [[{other.id}]] — {row['episode_count']} episode(s)")
            lines.append("")

        recent = summary.get("recent_mentions") or []
        if recent:
            lines.append("## Recent mentions")
            lines.append("")
            for ctx in recent[:5]:
                m = ctx.mention
                ts = m.start_ms // 1000
                lines.append(
                    f"- [{ctx.episode_title} @ {ts}s]({_episode_link(ctx)}): " f"{m.quote_excerpt.strip()[:280]}"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _episode_link(ctx) -> str:
    """``thestill://`` deeplink to the moment this mention starts."""
    seconds = ctx.mention.start_ms // 1000
    return f"thestill://episode/{ctx.episode_id}?t={seconds}"


def _slug_from_id(entity_id: str) -> str:
    """``"person:elon-musk"`` → ``"elon-musk"``."""
    return entity_id.split(":", 1)[-1]


def _yaml_string(value: str) -> str:
    """Quote a YAML string only when it contains awkward characters."""
    if not value:
        return '""'
    needs_quote = any(c in value for c in (":", "#", "[", "]", "{", "}", "'", '"', "\n", "\r"))
    if needs_quote or value.strip() != value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _segmap_to_dict(entry: SegmapEntry) -> dict:
    return {
        "seg_id": entry.seg_id,
        "line_start": entry.line_start,
        "line_end": entry.line_end,
        "byte_start": entry.byte_start,
        "byte_end": entry.byte_end,
        "start_ms": entry.start_ms,
        "end_ms": entry.end_ms,
    }


def _utf8_size(text: str) -> int:
    """Byte length of ``text`` in UTF-8."""
    return len(text.encode("utf-8"))


def _write_if_changed(path: Path, content: str) -> bool:
    """Write ``content`` to ``path`` only when the bytes would differ.

    Idempotency invariant: re-running a render against unchanged inputs
    must NOT touch the file's mtime. Avoids triggering qmd reindexing
    of files whose content didn't change.
    """
    payload = content.encode("utf-8")
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError:
            existing = None
        if existing == payload:
            return False
    path.write_bytes(payload)
    return True
