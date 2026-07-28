# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Link host/guest/recurring roles from LLM-extracted facts files into
the entity layer.

The summarize step already produces ``data/podcast_facts/{slug}.facts.md``
and ``data/episode_facts/{podcast_slug}/{episode_slug}.facts.md`` with
sections like ``## Hosts``, ``## Guest(s)``, and ``## Recurring Roles``.
This module parses those sections, resolves each name against the
``entities`` table (creating a ``person`` entity if no match exists),
and writes ``podcasts.host_entity_ids`` / ``podcasts.recurring_entity_ids``
/ ``episodes.guest_entity_ids`` via the existing repository setters.

No LLM calls — the LLM has already named the people; this is the
parse-and-link bridge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from structlog import get_logger

from ..models.entities import EntityRecord, EntityType
from ..repositories.sqlite_entity_repository import SqliteEntityRepository
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..utils.path_manager import PathManager
from ..utils.slug import generate_slug

logger = get_logger(__name__)


# Generic role labels the LLM emits for non-person voices (sponsor reads,
# bumpers, etc.). These are valid roles for the transcript, not entities
# we want to track in the search index.
_GENERIC_ROLE_NAMES = frozenset(
    name.lower()
    for name in (
        "Ad Narrator",
        "Ad Reader",
        "Sponsor",
        "Sponsor Narrator",
        "Sponsor Read",
        "Narrator",
        "Announcer",
        "Voiceover",
        "Voice Over",
        "Promo Narrator",
        "Theme Music",
    )
)

# Podcast-level hosts come from a single-episode LLM extraction, so a
# one-off guest co-host can be misfiled under ``## Hosts`` (e.g. a guest
# who co-interviews on the first episode cleaned). Episode facts files
# carry per-episode ``Name (Host)`` speaker-mapping annotations; we use
# them as corroborating evidence and demote podcast-level host candidates
# who almost never appear as a host across episodes. Thresholds are
# deliberately conservative: mention-level speaking coverage is unusable
# here (legitimate hosts often have zero linked speaking mentions), and a
# false demotion is worse than a false keep.
HOST_EVIDENCE_MIN_FILES = 5
HOST_EVIDENCE_MIN_APPEARANCES = 2

# Bullet in an episode facts speaker mapping naming a host, e.g.
# ``- SPEAKER_01: John Collison (Host)`` or ``- Chuck Nice (Co-Host)``.
_SPEAKER_HOST_RE = re.compile(
    r"^\s*[-*]\s*(?:SPEAKER[_ ]?\S+\s*:\s*)?(?P<name>.+?)\s*\(\s*(?:co[-\s]?)?host\s*\)\s*$",
    re.IGNORECASE,
)

# Heading variants we accept for each section. Lowercased before lookup
# so a stray capitalisation doesn't drop a section.
_HOST_HEADINGS = ("hosts", "host")
_GUEST_HEADINGS = ("guests", "guest", "guest(s)")
_RECURRING_HEADINGS = ("recurring roles", "recurring", "recurring voices", "recurring guests")


@dataclass
class FactsRoles:
    """Roles parsed from a single facts file."""

    hosts: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # (name, bio)
    guests: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    recurring: List[Tuple[str, Optional[str]]] = field(default_factory=list)


@dataclass
class LinkResult:
    """Outcome of linking one podcast or episode."""

    target_id: str
    hosts: List[str] = field(default_factory=list)
    guests: List[str] = field(default_factory=list)
    recurring: List[str] = field(default_factory=list)
    created_entities: List[str] = field(default_factory=list)
    skipped_names: List[str] = field(default_factory=list)
    demoted_hosts: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*?)\s*$")
_HEADING_RE = re.compile(r"^\s*##+\s+(.*?)\s*$")


def parse_facts_file(path: Path) -> FactsRoles:
    """Parse a ``.facts.md`` file into its host/guest/recurring lists.

    Treats unknown ``##`` sections as section terminators — we only
    consume bullets directly under a known heading.
    """
    if not path.exists():
        return FactsRoles()
    content = path.read_text(encoding="utf-8", errors="replace")
    roles = FactsRoles()
    current_bucket: Optional[str] = None
    for line in content.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            heading = heading_match.group(1).strip().lower()
            if heading in _HOST_HEADINGS:
                current_bucket = "hosts"
            elif heading in _GUEST_HEADINGS:
                current_bucket = "guests"
            elif heading in _RECURRING_HEADINGS:
                current_bucket = "recurring"
            else:
                current_bucket = None
            continue
        if current_bucket is None:
            continue
        bullet_match = _BULLET_RE.match(line)
        if not bullet_match:
            continue
        name, bio = _split_name_and_bio(bullet_match.group(1))
        if not name:
            continue
        getattr(roles, current_bucket).append((name, bio))
    return roles


def _split_name_and_bio(text: str) -> Tuple[str, Optional[str]]:
    """Split ``"Name - bio fragment"`` into ``(name, bio)``.

    Splits on the first ``" - "`` so multi-dash bios don't get
    truncated. Trailing parenthetical role markers (e.g. ``"Sarah Paine
    (Guest)"``) are stripped from the name — the bracket content is
    redundant once we're writing into a typed role column.
    """
    text = text.strip()
    if not text:
        return "", None
    name, bio = text, None
    if " - " in text:
        name, bio = text.split(" - ", 1)
        name = name.strip()
        bio = bio.strip() or None
    # Strip trailing "(Role)" annotation from the name.
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return name, bio


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _is_real_person_name(name: str) -> bool:
    """Filter out generic role labels and obvious non-person bullets.

    The LLM occasionally lists "Ad Narrator" or "Sponsor" under a roles
    section — those are voices in the audio, not entities we want to
    track in search.
    """
    cleaned = name.strip()
    if not cleaned:
        return False
    if cleaned.lower() in _GENERIC_ROLE_NAMES:
        return False
    # A real person's name almost always has at least one space and a
    # capital letter. This is a soft filter — a single-name guest like
    # "Madonna" would be skipped, which is acceptable for v1.
    if " " not in cleaned:
        return False
    if not any(ch.isupper() for ch in cleaned):
        return False
    return True


def _resolve_or_create_person(
    entity_repo: SqliteEntityRepository,
    name: str,
    bio: Optional[str],
) -> Tuple[str, bool]:
    """Return ``(entity_id, created)``.

    Tries existing entity by exact canonical_name / alias / id (any
    type). If found we adopt that entity even if its type is not
    ``person`` — the existing data is authoritative; we don't want to
    fork a duplicate. If nothing matches we mint a new ``person:`` entity
    seeded with the LLM-supplied bio as ``description``.
    """
    existing = entity_repo.find_entity_by_name(name)
    if existing is not None:
        return existing.id, False
    new_id = f"person:{generate_slug(name)}"
    record = EntityRecord(
        id=new_id,
        type=EntityType.PERSON,
        canonical_name=name,
        aliases=[],
        description=bio,
    )
    entity_repo.upsert_entity(record)
    return new_id, True


# ---------------------------------------------------------------------------
# Host evidence (episode facts speaker mappings)
# ---------------------------------------------------------------------------


def collect_host_evidence(
    *,
    podcast_slug: str,
    entity_repo: SqliteEntityRepository,
    path_manager: PathManager,
) -> Tuple[int, dict]:
    """Aggregate ``Name (Host)`` speaker-mapping annotations across a
    podcast's episode facts files.

    Returns ``(files_with_host_annotations, {entity_id: n_files})`` where
    each file counts an entity at most once. Names are resolved read-only
    via ``find_entity_by_name`` — unresolved name variants simply
    contribute no evidence; nothing is created here.
    """
    episode_dir = path_manager.episode_facts_dir() / podcast_slug
    if not episode_dir.is_dir():
        return 0, {}
    files_with_hosts = 0
    counts: dict = {}
    name_cache: dict = {}
    for facts_file in sorted(episode_dir.glob("*.facts.md")):
        try:
            content = facts_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_entity_ids = set()
        for line in content.splitlines():
            match = _SPEAKER_HOST_RE.match(line)
            if not match:
                continue
            name = match.group("name").strip()
            if not _is_real_person_name(name):
                continue
            if name not in name_cache:
                existing = entity_repo.find_entity_by_name(name)
                name_cache[name] = existing.id if existing is not None else None
            if name_cache[name] is not None:
                file_entity_ids.add(name_cache[name])
        if file_entity_ids:
            files_with_hosts += 1
            for entity_id in file_entity_ids:
                counts[entity_id] = counts.get(entity_id, 0) + 1
    return files_with_hosts, counts


def _filter_hosts_by_evidence(hosts: List[str], evidence_files: int, counts: dict) -> Tuple[List[str], List[str]]:
    """Split podcast-level host candidates into ``(kept, demoted)``.

    Skips entirely (keeps everyone) when there aren't enough annotated
    episode facts to judge, and when demotion would empty the host list —
    an all-fail result means the evidence itself is unreliable for this
    podcast, not that it has no hosts.
    """
    if evidence_files < HOST_EVIDENCE_MIN_FILES:
        return hosts, []
    kept = [h for h in hosts if counts.get(h, 0) >= HOST_EVIDENCE_MIN_APPEARANCES]
    if not kept:
        return hosts, []
    demoted = [h for h in hosts if h not in kept]
    return kept, demoted


# ---------------------------------------------------------------------------
# Linkers
# ---------------------------------------------------------------------------


def link_podcast_roles(
    *,
    podcast_id: str,
    podcast_slug: str,
    entity_repo: SqliteEntityRepository,
    path_manager: PathManager,
) -> LinkResult:
    """Parse the podcast facts file and write hosts + recurring."""
    facts_path = path_manager.podcast_facts_file(podcast_slug)
    roles = parse_facts_file(facts_path)
    result = LinkResult(target_id=podcast_id)
    result.hosts, result.created_entities, result.skipped_names = _resolve_role_list(
        entity_repo, roles.hosts, accumulator_for_created=result.created_entities
    )
    recurring_ids, created_recurring, skipped_recurring = _resolve_role_list(
        entity_repo, roles.recurring, accumulator_for_created=result.created_entities
    )
    result.recurring = recurring_ids
    result.created_entities.extend(created_recurring)
    result.skipped_names.extend(skipped_recurring)
    if result.hosts:
        evidence_files, host_counts = collect_host_evidence(
            podcast_slug=podcast_slug,
            entity_repo=entity_repo,
            path_manager=path_manager,
        )
        result.hosts, result.demoted_hosts = _filter_hosts_by_evidence(result.hosts, evidence_files, host_counts)
        if result.demoted_hosts:
            logger.warning(
                "podcast_hosts_demoted",
                podcast_id=podcast_id,
                podcast_slug=podcast_slug,
                demoted=result.demoted_hosts,
                evidence_files=evidence_files,
                appearances={h: host_counts.get(h, 0) for h in result.demoted_hosts},
            )
        entity_repo.set_podcast_hosts(podcast_id, result.hosts)
    if result.recurring:
        entity_repo.set_podcast_recurring(podcast_id, result.recurring)
    logger.info(
        "podcast_roles_linked",
        podcast_id=podcast_id,
        podcast_slug=podcast_slug,
        hosts=len(result.hosts),
        recurring=len(result.recurring),
        created_entities=len(set(result.created_entities)),
        skipped=len(result.skipped_names),
    )
    return result


def link_episode_roles(
    *,
    episode_id: str,
    podcast_slug: str,
    episode_slug: str,
    entity_repo: SqliteEntityRepository,
    path_manager: PathManager,
) -> LinkResult:
    """Parse the episode facts file and write guests."""
    facts_path = path_manager.episode_facts_file(podcast_slug, episode_slug)
    roles = parse_facts_file(facts_path)
    result = LinkResult(target_id=episode_id)
    result.guests, result.created_entities, result.skipped_names = _resolve_role_list(
        entity_repo, roles.guests, accumulator_for_created=result.created_entities
    )
    if result.guests:
        entity_repo.set_episode_guests(episode_id, result.guests)
    logger.info(
        "episode_roles_linked",
        episode_id=episode_id,
        episode_slug=episode_slug,
        guests=len(result.guests),
        created_entities=len(set(result.created_entities)),
        skipped=len(result.skipped_names),
    )
    return result


def _resolve_role_list(
    entity_repo: SqliteEntityRepository,
    items: Iterable[Tuple[str, Optional[str]]],
    *,
    accumulator_for_created: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    """Resolve a list of ``(name, bio)`` pairs to entity ids.

    Returns ``(entity_ids, created_ids, skipped_names)``. ``entity_ids``
    is deduplicated while preserving first-occurrence order so the
    resulting JSON column reads in the LLM's preferred order.
    """
    entity_ids: List[str] = []
    created_ids: List[str] = []
    skipped: List[str] = []
    seen_ids: set = set()
    for name, bio in items:
        if not _is_real_person_name(name):
            skipped.append(name)
            continue
        entity_id, created = _resolve_or_create_person(entity_repo, name, bio)
        if entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)
        entity_ids.append(entity_id)
        if created:
            created_ids.append(entity_id)
    return entity_ids, created_ids, skipped


# ---------------------------------------------------------------------------
# Backfill orchestrator
# ---------------------------------------------------------------------------


@dataclass
class BackfillSummary:
    podcasts_processed: int = 0
    podcasts_with_hosts: int = 0
    episodes_processed: int = 0
    episodes_with_guests: int = 0
    entities_created: int = 0
    skipped_names: List[str] = field(default_factory=list)


def backfill_all_roles(
    *,
    podcast_repo: SqlitePodcastRepository,
    entity_repo: SqliteEntityRepository,
    path_manager: PathManager,
) -> BackfillSummary:
    """One-shot pass over every podcast and episode in the DB.

    Idempotent — re-running just rewrites the same JSON values. Names
    that fail the ``_is_real_person_name`` filter are aggregated into
    ``skipped_names`` so the operator can spot-check whether anyone
    real is being missed.
    """
    summary = BackfillSummary()
    podcasts = podcast_repo.get_all()
    for podcast in podcasts:
        if not podcast.slug:
            continue
        summary.podcasts_processed += 1
        result = link_podcast_roles(
            podcast_id=podcast.id,
            podcast_slug=podcast.slug,
            entity_repo=entity_repo,
            path_manager=path_manager,
        )
        if result.hosts:
            summary.podcasts_with_hosts += 1
        summary.entities_created += len(set(result.created_entities))
        summary.skipped_names.extend(result.skipped_names)
        for episode in podcast.episodes:
            if not episode.slug:
                continue
            summary.episodes_processed += 1
            ep_result = link_episode_roles(
                episode_id=episode.id,
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                entity_repo=entity_repo,
                path_manager=path_manager,
            )
            if ep_result.guests:
                summary.episodes_with_guests += 1
            summary.entities_created += len(set(ep_result.created_entities))
            summary.skipped_names.extend(ep_result.skipped_names)
    return summary
