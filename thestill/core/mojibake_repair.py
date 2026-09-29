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

"""Find and undo double-encoded UTF-8 ("mojibake") in everything Thestill persists.

``thestill repair-mojibake`` walks the text the pipeline stores — podcast
and episode metadata, entity names and mentions, facts files, cleaned
transcripts, summaries — and repairs ``"JungestÃ¥l"``-style damage with
``utils.text_sanitizer.repair_mojibake``. The search index is not patched
in place: chunk rows carry an embedding of their text, so every episode
whose cleaned-transcript sidecar changed (or whose chunk rows are damaged)
is re-written through the configured chunk writer, which re-embeds.

The damage entered through ``requests.Response.text`` guessing a feed
body's charset (fixed in ``media_source.decode_feed_body``); this module
is the repair for what was stored before that fix. Dry run by default —
the report lists every change and the caller decides whether to apply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from structlog import get_logger

from ..repositories.factory import make_chunk_writer, uses_postgres
from ..utils.text_sanitizer import repair_mojibake

if TYPE_CHECKING:
    from ..utils.config import Config
    from ..utils.path_manager import PathManager

logger = get_logger()

_PREVIEW_CHARS = 72
_BATCH = 5000


@dataclass(frozen=True)
class ColumnSpec:
    """Text columns of one table, plus how to restrict the scan to one podcast.

    ``json_columns`` hold ``json.dumps`` lists of strings; the repositories
    write them with ``ensure_ascii`` on, so the mojibake sits behind
    ``\\u00c3`` escapes and has to be repaired inside the decoded list.
    ``podcast_scope`` is a predicate template (``{ph}`` = placeholder);
    ``None`` means the table is corpus-global and is skipped when a
    podcast filter is given.
    """

    table: str
    columns: Tuple[str, ...]
    json_columns: Tuple[str, ...] = ()
    podcast_scope: Optional[str] = None


TEXT_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("podcasts", ("title", "description", "author", "copyright"), podcast_scope="id = {ph}"),
    ColumnSpec(
        "episodes",
        ("title", "description", "description_html", "summary_preview"),
        podcast_scope="podcast_id = {ph}",
    ),
    ColumnSpec("entities", ("canonical_name", "description"), json_columns=("aliases",)),
    ColumnSpec(
        "entity_mentions",
        ("speaker", "surface_form", "surface_label", "quote_excerpt"),
        podcast_scope="episode_id IN (SELECT id FROM episodes WHERE podcast_id = {ph})",
    ),
)

_CHUNK_PODCAST_SCOPE = "episode_id IN (SELECT id FROM episodes WHERE podcast_id = {ph})"


@dataclass
class Change:
    """One repaired value: ``location`` is ``table.column`` or ``file``."""

    location: str
    key: str
    before: str
    after: str


@dataclass
class RepairReport:
    applied: bool
    changes: List[Change] = field(default_factory=list)
    # Episodes whose chunk rows were (or would be) re-written from the sidecar.
    chunk_episodes: List[str] = field(default_factory=list)
    chunks_rewritten: int = 0
    # Episodes with damaged chunk rows but no cleaned-transcript sidecar to rebuild from.
    chunks_without_sidecar: List[str] = field(default_factory=list)
    unreadable_files: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in self.changes:
            out[c.location] = out.get(c.location, 0) + 1
        return out

    @property
    def found_anything(self) -> bool:
        return bool(self.changes or self.chunk_episodes or self.chunks_without_sidecar)


def _preview(before: str, after: str) -> Tuple[str, str]:
    """First differing line of a repaired value, truncated for a report line."""
    for b, a in zip(before.split("\n"), after.split("\n")):
        if b != a:
            return b[:_PREVIEW_CHARS], a[:_PREVIEW_CHARS]
    return before[:_PREVIEW_CHARS], after[:_PREVIEW_CHARS]


class _Db:
    """The two placeholder dialects behind one ``connect()``; both commit on exit."""

    def __init__(self, config: "Config"):
        self._config = config
        self._postgres = uses_postgres(config)
        self.ph = "%s" if self._postgres else "?"

    def connect(self) -> Any:
        if self._postgres:
            from ..utils.postgres_ext import connect as pg_connect

            return pg_connect(self._config.database_url)
        from ..utils.sqlite_ext import connect as sqlite_connect

        return sqlite_connect(str(self._config.database_path))

    def scope(self, template: Optional[str]) -> Optional[str]:
        return template.format(ph=self.ph) if template else None

    def bind_json(self, value: Any) -> Any:
        """Wrap a decoded JSON value for a ``jsonb`` parameter (Postgres only)."""
        if self._postgres:
            from psycopg.types.json import Jsonb

            return Jsonb(value)
        return json.dumps(value)


def _iter_rows(
    conn: Any,
    db: _Db,
    table: str,
    columns: Sequence[str],
    *,
    where: Optional[str] = None,
    params: Sequence[Any] = (),
) -> Iterator[Any]:
    """Keyset-paginate ``SELECT id, columns FROM table`` so a 400k-row chunks
    table never has to be materialised at once."""
    last: Any = None
    while True:
        conds: List[str] = [where] if where else []
        args: List[Any] = list(params)
        if last is not None:
            conds.append(f"id > {db.ph}")
            args.append(last)
        sql = f"SELECT id, {', '.join(columns)} FROM {table}"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += f" ORDER BY id LIMIT {_BATCH}"
        rows = conn.execute(sql, tuple(args)).fetchall()
        if not rows:
            return
        yield from rows
        last = rows[-1]["id"]


def _repair_json_list(raw: Any) -> Tuple[Any, bool]:
    """Repair the strings inside a JSON list of strings.

    SQLite stores the column as ``json.dumps`` text (a ``str`` comes in, a
    ``str`` goes out); Postgres stores ``jsonb`` and psycopg hands over the
    decoded ``list`` (a ``list`` goes out, bound as ``Jsonb`` by the caller).
    Anything else passes through untouched.
    """
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except ValueError:
            return raw, False
    elif isinstance(raw, list):
        items = raw
    else:
        return raw, False
    if not isinstance(items, list):
        return raw, False
    changed = False
    fixed: List[Any] = []
    for item in items:
        if isinstance(item, str):
            new, did = repair_mojibake(item)
            changed = changed or did
            fixed.append(new)
        else:
            fixed.append(item)
    if not changed:
        return raw, False
    return (json.dumps(fixed) if isinstance(raw, str) else fixed), True


def _json_preview(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:_PREVIEW_CHARS]


class MojibakeRepairer:
    """Scan, and optionally repair, mojibake in files, rows and the chunk index."""

    def __init__(
        self,
        config: "Config",
        path_manager: "PathManager",
        podcast_repo: Any,
        embedding_model: Any = None,
    ):
        self._config = config
        self._path_manager = path_manager
        self._repo = podcast_repo
        self._embedding_model = embedding_model
        self._db = _Db(config)

    # ── entry point ────────────────────────────────────────────────────

    def run(self, *, apply: bool = False, podcast_id: Optional[str] = None) -> RepairReport:
        report = RepairReport(applied=apply)
        podcasts = self._podcasts(podcast_id)

        sidecar_by_episode: Dict[str, Path] = {}
        for p in podcasts:
            for e in p.episodes or []:
                if e.clean_transcript_json_path:
                    sidecar_by_episode[e.id] = self._path_manager.clean_transcript_file(e.clean_transcript_json_path)

        repaired_sidecars = self._repair_files(podcasts, report, apply)
        self._repair_columns(report, apply, podcast_id)
        damaged_chunks = self._episodes_with_damaged_chunks(podcast_id)
        self._rewrite_chunks(repaired_sidecars | damaged_chunks, sidecar_by_episode, report, apply)

        logger.info(
            "mojibake_repair_finished",
            applied=apply,
            podcast_id=podcast_id,
            changes=len(report.changes),
            chunk_episodes=len(report.chunk_episodes),
            chunks_without_sidecar=len(report.chunks_without_sidecar),
            unreadable_files=len(report.unreadable_files),
        )
        return report

    # ── podcasts ───────────────────────────────────────────────────────

    def _podcasts(self, podcast_id: Optional[str]) -> List[Any]:
        if podcast_id:
            podcast = self._repo.get(podcast_id)
            if podcast is None:
                raise ValueError(f"No podcast with id={podcast_id}")
            return [podcast]
        return list(self._repo.get_all())

    # ── files ──────────────────────────────────────────────────────────

    def _files(self, podcasts: Sequence[Any]) -> Iterator[Tuple[Path, Optional[str]]]:
        """Yield ``(path, episode_id)``; ``episode_id`` is set only for the JSON sidecar
        (the one file the chunk index is rebuilt from)."""
        pm = self._path_manager
        for p in podcasts:
            if p.slug:
                yield pm.podcast_facts_file(p.slug), None
            for e in p.episodes or []:
                if p.slug and e.slug:
                    yield pm.episode_facts_file(p.slug, e.slug), None
                if e.clean_transcript_path:
                    yield pm.clean_transcript_file(e.clean_transcript_path), None
                if e.clean_transcript_json_path:
                    yield pm.clean_transcript_file(e.clean_transcript_json_path), e.id
                if e.summary_path:
                    yield pm.summary_file(e.summary_path), None

    def _repair_files(self, podcasts: Sequence[Any], report: RepairReport, apply: bool) -> Set[str]:
        """Repair content files as text. JSON sidecars are written by pydantic
        with non-ASCII kept literal, so a text-level pass sees the mojibake
        and preserves the file's formatting."""
        repaired_sidecars: Set[str] = set()
        seen: Set[Path] = set()
        for path, episode_id in self._files(podcasts):
            if path in seen or not path.exists():
                continue
            seen.add(path)
            try:
                before = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                report.unreadable_files.append(self._path_manager.to_relative(path))
                logger.warning("mojibake_repair_file_not_utf8", path=str(path))
                continue
            after, changed = repair_mojibake(before)
            if not changed:
                continue
            b, a = _preview(before, after)
            report.changes.append(Change("file", self._path_manager.to_relative(path), b, a))
            if episode_id:
                repaired_sidecars.add(episode_id)
            if apply:
                path.write_text(after, encoding="utf-8")
        return repaired_sidecars

    # ── rows ───────────────────────────────────────────────────────────

    def _repair_columns(self, report: RepairReport, apply: bool, podcast_id: Optional[str]) -> None:
        db = self._db
        with db.connect() as conn:
            for spec in TEXT_COLUMNS:
                where = db.scope(spec.podcast_scope) if podcast_id else None
                if podcast_id and where is None:
                    continue  # corpus-global table, outside a per-podcast run
                params = (podcast_id,) if where else ()
                columns = spec.columns + spec.json_columns
                for row in _iter_rows(conn, db, spec.table, columns, where=where, params=params):
                    updates: Dict[str, Any] = {}
                    for col in spec.columns:
                        value = row[col]
                        if isinstance(value, str):
                            fixed, changed = repair_mojibake(value)
                            if changed:
                                updates[col] = fixed
                                b, a = _preview(value, fixed)
                                report.changes.append(Change(f"{spec.table}.{col}", str(row["id"]), b, a))
                    for col in spec.json_columns:
                        fixed, changed = _repair_json_list(row[col])
                        if changed:
                            updates[col] = db.bind_json(fixed) if isinstance(fixed, list) else fixed
                            report.changes.append(
                                Change(
                                    f"{spec.table}.{col}", str(row["id"]), _json_preview(row[col]), _json_preview(fixed)
                                )
                            )
                    if updates and apply:
                        assignments = ", ".join(f"{col} = {db.ph}" for col in updates)
                        conn.execute(
                            f"UPDATE {spec.table} SET {assignments} WHERE id = {db.ph}",
                            (*updates.values(), row["id"]),
                        )

    # ── chunks ─────────────────────────────────────────────────────────

    def _episodes_with_damaged_chunks(self, podcast_id: Optional[str]) -> Set[str]:
        db = self._db
        where = db.scope(_CHUNK_PODCAST_SCOPE) if podcast_id else None
        params = (podcast_id,) if where else ()
        damaged: Set[str] = set()
        with db.connect() as conn:
            for row in _iter_rows(conn, db, "chunks", ("episode_id", "text"), where=where, params=params):
                if str(row["episode_id"]) in damaged:
                    continue
                if repair_mojibake(row["text"])[1]:
                    damaged.add(str(row["episode_id"]))
        return damaged

    def _rewrite_chunks(
        self,
        episode_ids: Set[str],
        sidecar_by_episode: Dict[str, Path],
        report: RepairReport,
        apply: bool,
    ) -> None:
        if not episode_ids:
            return
        from ..models.annotated_transcript import AnnotatedTranscript

        writer = None
        for episode_id in sorted(episode_ids):
            sidecar = sidecar_by_episode.get(episode_id)
            if sidecar is None or not sidecar.exists():
                report.chunks_without_sidecar.append(episode_id)
                continue
            report.chunk_episodes.append(episode_id)
            if not apply:
                continue
            if writer is None:
                writer = make_chunk_writer(self._config, self._embedding_model)
            transcript = AnnotatedTranscript.model_validate_json(sidecar.read_text(encoding="utf-8"))
            inserted = writer.write_episode(episode_id, transcript, force=True)
            report.chunks_rewritten += inserted
            logger.info("mojibake_repair_chunks_rewritten", episode_id=episode_id, rows=inserted)
