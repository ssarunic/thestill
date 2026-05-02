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

"""Spec #28 §2.10 — Obsidian-friendly per-entity Markdown pages.

Surfaces the entity database as ``data/corpus/{persons,companies,topics}/<slug>.md``
so power users can browse the corpus in Obsidian (graph view, wiki-link
navigation). This is a side channel — search runs against the
``chunks`` SQLite tables, not these files.

Pages are regenerated out-of-band via ``thestill rebuild-entity-pages``;
they are NOT touched per-episode in the REINDEX stage. Idempotent —
unchanged files don't get their mtime bumped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from structlog import get_logger

from ..models.entities import EntityRecord, EntityType
from ..repositories.sqlite_entity_repository import SqliteEntityRepository
from ..utils.path_manager import PathManager

logger = get_logger(__name__)


_WIKIDATA_URL = "https://www.wikidata.org/wiki/{qid}"

_TYPE_TO_RENDERED_DIR = {
    EntityType.PERSON: "persons",
    EntityType.COMPANY: "companies",
    EntityType.TOPIC: "topics",
}


@dataclass
class WrittenPaths:
    """Tally of files actually changed during a render run."""

    written: List[Path] = field(default_factory=list)
    skipped_unchanged: List[Path] = field(default_factory=list)


class EntityPageWriter:
    """Writes per-entity Markdown pages from SQLite entity records.

    ``product`` entities are silently skipped — they don't get
    Obsidian-browsable pages in v1.
    """

    def __init__(
        self,
        path_manager: PathManager,
        entity_repository: SqliteEntityRepository,
    ):
        self.path_manager = path_manager
        self.entity_repository = entity_repository

    def write_entity_page(self, entity: EntityRecord) -> WrittenPaths:
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

    def write_all(self, *, entity_type: Optional[EntityType] = None) -> WrittenPaths:
        """Render every person/company/topic entity (or one type)."""
        result = WrittenPaths()
        types_to_render = (entity_type,) if entity_type else (EntityType.PERSON, EntityType.COMPANY, EntityType.TOPIC)
        for et in types_to_render:
            if et not in _TYPE_TO_RENDERED_DIR:
                continue
            for entity in self.entity_repository.list_entities_by_type(et.value):
                merged = self.write_entity_page(entity)
                result.written.extend(merged.written)
                result.skipped_unchanged.extend(merged.skipped_unchanged)
        return result


def _render_entity_page(*, entity: EntityRecord, summary: Optional[dict]) -> str:
    lines: List[str] = ["---"]
    lines.append(f"type: {entity.type.value}")
    lines.append(f"id: {entity.id}")
    lines.append(f"canonical_name: {_yaml_string(entity.canonical_name)}")
    if entity.wikidata_qid:
        lines.append(f"wikidata_qid: {entity.wikidata_qid}")
    if entity.aliases:
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
                lines.append(f"- [{ctx.episode_title} @ {ts}s]({_episode_link(ctx)}): {m.quote_excerpt.strip()[:280]}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _episode_link(ctx) -> str:
    seconds = ctx.mention.start_ms // 1000
    return f"thestill://episode/{ctx.episode_id}?t={seconds}"


def _slug_from_id(entity_id: str) -> str:
    return entity_id.split(":", 1)[-1]


def _yaml_string(value: str) -> str:
    if not value:
        return '""'
    needs_quote = any(c in value for c in (":", "#", "[", "]", "{", "}", "'", '"', "\n", "\r"))
    if needs_quote or value.strip() != value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write_if_changed(path: Path, content: str) -> bool:
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
