"""Spec #28 §2.5 — Python client for qmd's MCP transport.

Speaks JSON-RPC over the ``qmd mcp`` subprocess (stdio) and exposes a
single ``search`` method that consumes a list of typed sub-queries and
returns ``CitationRow``s.

Hits are mapped from qmd's ``(file, snippet)`` shape back to
``(episode_id, segment_id, start_ms, end_ms)`` by binary-searching the
``<id>.segmap.json`` sidecar that ``corpus_writer.py`` produces. The
load-bearing line number comes from the leading ``<line>:`` token of
``snippet`` (Phase 0.1 spike).

Hits with no resolvable segment (front-matter region, between-block
whitespace) are dropped from results, logged at info.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from structlog import get_logger

from ..models.entities import CitationRow, MatchType
from ..repositories.sqlite_entity_repository import SqliteEntityRepository

logger = get_logger(__name__)


DEFAULT_COLLECTION = "thestill-corpus"

# Snippet line-marker shapes:
#   "27: @@ -26,4 @@ (25 before, 52 after)"  — diff hunk header (skip)
#   "28:     ip_address      INET,"          — content line (use this)
_LINE_PREFIX_RE = re.compile(r"^(\d+):")
# A diff-hunk-header line has "@@ ... @@" after the line prefix.
_DIFF_HUNK_RE = re.compile(r"^\d+:\s+@@")


@dataclass(frozen=True)
class QmdHit:
    """Raw qmd search hit before segment resolution."""

    docid: str
    file: str  # collection-relative path, e.g. "thestill-corpus/episodes/<slug>/<id>.md"
    title: str
    score: float
    snippet: str


@dataclass(frozen=True)
class ResolvedHit:
    """A qmd hit mapped to a transcript segment.

    Returned by ``QmdClient.search`` so callers (the MCP tool, the
    REST endpoint) can render either a ``CitationRow`` or a richer
    structure. ``segment_meta`` is the raw segmap row for the segment
    a hit landed inside.
    """

    hit: QmdHit
    episode_id: str
    podcast_slug: str
    segment_id: int
    start_ms: int
    end_ms: int
    line_number: int


class QmdClient:
    """Stdio MCP wrapper around the ``qmd`` binary.

    One short-lived subprocess per ``search`` call — qmd's MCP server
    is fast to boot (~50 ms). Persistent processes would let us
    amortize startup but introduce process-lifecycle complexity that
    isn't worth it at the rate the search tool is called from the web
    UI / Claude Desktop.
    """

    def __init__(
        self,
        *,
        corpus_dir: Path,
        collection: str = DEFAULT_COLLECTION,
        qmd_binary: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ):
        self.corpus_dir = Path(corpus_dir)
        self.collection = collection
        self.timeout_seconds = timeout_seconds
        binary = qmd_binary or shutil.which("qmd")
        if binary is None:
            raise FileNotFoundError("qmd binary not found on PATH. Install qmd (https://qmd.dev) " "and re-run.")
        self.qmd_binary = binary

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        searches: Sequence[dict],
        *,
        limit: int = 10,
        intent: Optional[str] = None,
        min_score: float = 0.0,
        rerank: bool = True,
    ) -> List[ResolvedHit]:
        """Run a query and return resolved hits in qmd-relevance order.

        ``searches`` is qmd's ``[{type:..., query:...}, ...]`` payload.
        Single-element lex-only payload = lexical mode; lex+vec
        (or lex+vec+hyde) = hybrid mode. The handler-side wrapper in
        the MCP tool layer (Phase 2.6) decides which.
        """
        raw_hits = self._raw_search(searches, limit=limit, intent=intent, min_score=min_score, rerank=rerank)
        resolved: List[ResolvedHit] = []
        dropped = 0
        for hit in raw_hits:
            res = self._resolve_hit(hit)
            if res is None:
                dropped += 1
                continue
            resolved.append(res)
        if dropped:
            logger.info(
                "qmd_hits_dropped_unresolvable",
                dropped=dropped,
                kept=len(resolved),
                note="hits in front-matter or between-segment whitespace",
            )
        return resolved

    # ------------------------------------------------------------------
    # Internals — JSON-RPC transport
    # ------------------------------------------------------------------

    def _raw_search(
        self,
        searches: Sequence[dict],
        *,
        limit: int,
        intent: Optional[str],
        min_score: float,
        rerank: bool,
    ) -> List[QmdHit]:
        args: dict = {
            "searches": list(searches),
            "limit": limit,
            "minScore": min_score,
            "rerank": rerank,
            "collections": [self.collection],
        }
        if intent:
            args["intent"] = intent
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "query", "arguments": args}}
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "thestill", "version": "0"},
            },
        }
        stdin_payload = json.dumps(init) + "\n" + json.dumps(request) + "\n"
        try:
            proc = subprocess.run(
                [self.qmd_binary, "mcp"],
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("qmd_mcp_timeout", seconds=self.timeout_seconds)
            return []
        if proc.returncode != 0:
            logger.warning(
                "qmd_mcp_nonzero_exit",
                returncode=proc.returncode,
                stderr=(proc.stderr or "")[:300],
            )
        # Each newline-delimited line is one JSON-RPC frame.
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if frame.get("id") != 2:
                continue
            return _hits_from_frame(frame)
        return []

    # ------------------------------------------------------------------
    # Internals — hit → segment resolution
    # ------------------------------------------------------------------

    def _resolve_hit(self, hit: QmdHit) -> Optional[ResolvedHit]:
        """Map a qmd hit's snippet line back to a segment.

        Returns ``None`` if:
        - the hit's file is not under ``episodes/`` (e.g. an entity page)
        - the snippet contains no usable ``<line>:`` marker
        - the line falls outside any segment in the segmap (front-matter
          region, blank line between blocks)
        """
        path_relative = _strip_collection_prefix(hit.file, self.collection)
        # Only episode-page hits have a segmap. Entity-page hits are
        # informational and don't carry a transcript timestamp.
        if not path_relative.startswith("episodes/"):
            return None
        md_path = self.corpus_dir / path_relative
        segmap_path = md_path.with_suffix(".segmap.json")
        if not segmap_path.exists():
            return None
        line = _first_content_line(hit.snippet)
        if line is None:
            return None
        try:
            segmap = json.loads(segmap_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        segment = _segment_for_line(segmap, line)
        if segment is None:
            return None
        # path_relative looks like "episodes/<podcast-slug>/<episode-id>.md"
        parts = path_relative.split("/")
        if len(parts) < 3:
            return None
        podcast_slug = parts[1]
        episode_id = parts[2].rsplit(".", 1)[0]
        return ResolvedHit(
            hit=hit,
            episode_id=episode_id,
            podcast_slug=podcast_slug,
            segment_id=segment["seg_id"],
            start_ms=segment["start_ms"],
            end_ms=segment["end_ms"],
            line_number=line,
        )

    # ------------------------------------------------------------------
    # CitationRow rendering
    # ------------------------------------------------------------------

    def to_citation_rows(
        self,
        hits: Iterable[ResolvedHit],
        *,
        repository: SqliteEntityRepository,
    ) -> List[CitationRow]:
        """Hydrate resolved hits into citation-shaped wire rows.

        Joins each hit with the episode + podcast metadata required
        by the citation contract (Strategy §4). The match_type is
        inferred from the qmd score: by convention we tag a hit
        ``MatchType.LEXICAL`` when the score is exactly its raw
        BM25 weight (lex-only path), otherwise ``MatchType.SEMANTIC``.
        Callers that know the mode (handler layer) override.
        """
        if not hits:
            return []
        # Single connection, batched lookup of episode+podcast rows.
        episode_ids = list({h.episode_id for h in hits})
        with repository._get_connection() as conn:  # noqa: SLF001 — internal access on purpose
            placeholders = ",".join("?" * len(episode_ids))
            rows = conn.execute(
                f"""
                SELECT e.id AS episode_id, e.title AS episode_title,
                       e.pub_date AS episode_pub_date,
                       p.id AS podcast_id, p.title AS podcast_title
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.id IN ({placeholders})
                """,
                episode_ids,
            ).fetchall()
        ep_meta = {r["episode_id"]: r for r in rows}
        out: List[CitationRow] = []
        for h in hits:
            meta = ep_meta.get(h.episode_id)
            if meta is None:
                continue
            seconds = h.start_ms // 1000
            out.append(
                CitationRow(
                    episode_id=h.episode_id,
                    podcast_id=meta["podcast_id"],
                    podcast_title=meta["podcast_title"],
                    episode_title=meta["episode_title"],
                    published_at=meta["episode_pub_date"],
                    start_ms=h.start_ms,
                    end_ms=h.end_ms,
                    speaker=None,
                    quote=h.hit.snippet[:600],
                    score=h.hit.score,
                    match_type=MatchType.SEMANTIC,
                    deeplink=f"thestill://episode/{h.episode_id}?t={seconds}",
                    web_url=f"/episodes/{h.episode_id}?t={seconds}",
                )
            )
        return out


# ----------------------------------------------------------------------
# Helpers (testable without qmd)
# ----------------------------------------------------------------------


def _hits_from_frame(frame: dict) -> List[QmdHit]:
    """Pull the ``QmdHit`` list out of a JSON-RPC ``tools/call`` reply."""
    result = frame.get("result") or {}
    structured = result.get("structuredContent") or {}
    out: List[QmdHit] = []
    for r in structured.get("results") or []:
        out.append(
            QmdHit(
                docid=r.get("docid", ""),
                file=r.get("file", ""),
                title=r.get("title", ""),
                score=float(r.get("score") or 0.0),
                snippet=r.get("snippet") or "",
            )
        )
    return out


def _first_content_line(snippet: str) -> Optional[int]:
    """Return the first non-diff-header line number in ``snippet``.

    qmd's snippet format starts with a diff hunk like
    ``"27: @@ -26,4 @@ (25 before, 52 after)"`` followed by content
    lines tagged ``"<line>: <text>"``. We want the first content line.
    """
    for raw in snippet.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _DIFF_HUNK_RE.match(line):
            continue
        match = _LINE_PREFIX_RE.match(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _segment_for_line(segmap: List[dict], line: int) -> Optional[dict]:
    """Find the segmap entry whose ``[line_start, line_end]`` brackets ``line``.

    Linear scan is fine: episodes have at most a few hundred segments;
    the JSON parse cost dominates. Binary search would be a micro-opt.
    """
    for entry in segmap:
        start = entry.get("line_start", 0)
        end = entry.get("line_end", 0)
        if start <= line <= end:
            return entry
    return None


def _strip_collection_prefix(file_path: str, collection: str) -> str:
    """qmd returns paths like ``"thestill-corpus/episodes/.../id.md"``.

    Strip the leading collection name so the remainder joins onto
    ``corpus_dir`` to give the on-disk path.
    """
    prefix = collection + "/"
    if file_path.startswith(prefix):
        return file_path[len(prefix) :]
    return file_path
