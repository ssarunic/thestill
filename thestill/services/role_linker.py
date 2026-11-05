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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from structlog import get_logger

from ..models.entities import EntityRecord, EntityType
from ..models.facts import AD_WORD
from ..repositories.sqlite_entity_repository import SqliteEntityRepository
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..utils.file_storage import FileStorage
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

# Any speaker-mapping bullet, e.g. ``- SPEAKER_08: Evan Gershkovich (Guest)``
# or ``- SPEAKER_03: Ad Narrator 1`` — name plus optional ``(Role)``.
_SPEAKER_MAP_RE = re.compile(
    r"^\s*[-*]\s*SPEAKER[_ ]?\S+\s*:\s*(?P<name>.+?)\s*(?:\((?P<role>[^)]*)\))?\s*$",
    re.IGNORECASE,
)

# A named voice that only reads an advert is not a guest. The extractor
# is told so, but it has filed "Evan Gershkovich - Journalist (Ad Segment)"
# under Guest(s) — the author reading from his book in a promo — and the
# linker then made him a guest of the episode. A role annotation (the
# bracket on the bullet, or the speaker mapping's) is a single word or
# two, so any ad word in it is decisive; a bio is free text, so only an
# ad *phrase* counts there ("ad segment", "(ad)", "in an advert",
# "book promo"), not a lone word a real guest's job title may contain
# ("Head of Ad Sales").
_AD_ROLE_RE = re.compile(rf"\b{AD_WORD}\b", re.IGNORECASE)
_AD_BIO_RE = re.compile(
    rf"\([^)]*\b{AD_WORD}\b[^)]*\)"
    rf"|\b{AD_WORD}\s+(?:segments?|reads?|breaks?|spots?|narrators?|voices?|copy|messages?|sections?)\b"
    rf"|\bin\s+(?:an?|the)\s+{AD_WORD}\b"
    rf"|\b(?:book|podcast|product|sponsor|newspaper)\s+{AD_WORD}\b",
    re.IGNORECASE,
)

# A voice the conversation plays rather than one taking part in it: a
# campaign clip, a news soundbite, a caller. The extractor is told to
# annotate those "Name (Archival Audio)" / "Name (Listener)" rather than
# "(Guest)" (facts_extractor's AD VOICES ARE NOT GUESTS rule), but still
# lists some of them under Guest(s); a role annotation naming one is
# decisive, as an ad word is. The same words as the frontend's
# ROLE_ONLY_SPEAKER in buildPeople.ts.
_CLIP_ROLE_RE = re.compile(
    r"\b(?:archival|archive|clips?|soundbites?|footage|recording|listener|caller|voicemail)\b",
    re.IGNORECASE,
)

# Facts written before the clip rule label every clip voice "(Guest)",
# so the role annotation cannot catch them; how much they speak can. A
# guest takes part in the main discussion: on the Ezra Klein episode that
# surfaced this, Chris Hayes has 41% of the speaker rows and 42% of the
# words, the seven politicians in its clips at most 2.4% of either. A
# voice is a clip only when it is quiet on both counts — a presenter
# asking many short questions has the rows (Kelly Somers on Football
# Daily, 13% of rows, 3% of words), a guest with few long answers has
# the words (John Bishop on Parenting Hell, 5% of rows, 15% of words).
# On 1,788 judged guests in the local corpus the pair drops 231: post-
# match interviews, news soundbites, vox pops, and sister-show promo
# voices the cleaner never labels as a speaker at all (no rows).
# Only judged when the transcript has enough rows under the speaker
# mapping's names to tell — a short episode, or one whose labels stayed
# SPEAKER_nn, says nothing about who spoke.
CLIP_VOICE_MIN_NAMED_ROWS = 40
CLIP_VOICE_MAX_ROW_SHARE = 0.05
CLIP_VOICE_MAX_WORD_SHARE = 0.03

# A co-host taken from the speaker mapping alone (``_cohosts``) has to
# show they took part, not just escape the clip test: Prof G episodes are
# too short for that test, and their mappings label promo voices for
# sister shows "Jake Sullivan (Host - The Long Game)" with a row or two.
# A co-host speaks at least this many rows and is not quiet by the clip
# shares; without a transcript none is added.
COHOST_MIN_ROWS = 5

# A speaker row in a cleaned transcript: ``[01:16] **Ezra Klein:** …``.
_SPEAKER_ROW_RE = re.compile(r"^\s*(?:\[[0-9:.]+\]\s*)?\*\*(?P<name>[^*\n]+?):\*\*(?P<text>.*)$")

# A speaker-mapping role naming a host: "Host", "Co-Host", "Host/SaaStr",
# "Guest Host". On a show whose co-hosts rotate (20VC's weekly trio with
# Jason Lemkin and Rory O'Driscoll beside Harry Stebbings) the extractor
# labels them host in the mapping and, told to leave regular hosts out,
# lists them under no Guest(s) — while the show's host list, extracted
# once, names only Harry. Such a name is taken as a participant of the
# episode (see ``link_episode_roles``).
_HOST_ROLE_RE = re.compile(r"\bhost\b", re.IGNORECASE)

# A mapping "name" that is a placeholder, not a person: "Unknown Speaker",
# or the Croatian "Nepoznati voditelj" (unknown host) the extractor writes
# for an unnamed host — it passes ``_is_real_person_name`` (two words,
# capitals) but names nobody.
_PLACEHOLDER_NAME_RE = re.compile(
    r"\b(?:unknown|unidentified|unnamed|speaker|host|nepoznat\w*|voditelj\w*)\b|/",
    re.IGNORECASE,
)

# Heading variants we accept for each section. Lowercased before lookup
# so a stray capitalisation doesn't drop a section.
_HOST_HEADINGS = ("hosts", "host")
_GUEST_HEADINGS = ("guests", "guest", "guest(s)")
_RECURRING_HEADINGS = ("recurring roles", "recurring", "recurring voices", "recurring guests")
_SPEAKER_MAPPING_HEADINGS = ("speaker mapping",)


@dataclass
class FactsRoles:
    """Roles parsed from a single facts file."""

    hosts: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # (name, bio)
    guests: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    recurring: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    # Guest bullets dropped because their role annotation, bio, or
    # speaker-mapping role says the voice only read an advert.
    ad_voices: List[str] = field(default_factory=list)
    # Guest bullets dropped because their role annotation names a clip,
    # soundbite or caller rather than a participant.
    clip_voices: List[str] = field(default_factory=list)
    # ``## Speaker Mapping`` roles by casefolded name, e.g. {"evan gershkovich": "Ad Narrator"}.
    speaker_roles: Dict[str, str] = field(default_factory=dict)
    # Names the speaker mapping labels a host (``_HOST_ROLE_RE``), as
    # written and in mapping order, without repeats.
    mapped_hosts: List[str] = field(default_factory=list)


@dataclass
class SpeakerTally:
    """Speaker rows and spoken words per label in a cleaned transcript,
    keyed by the casefolded label without a trailing ``(Role)``."""

    rows: Counter = field(default_factory=Counter)
    words: Counter = field(default_factory=Counter)


@dataclass
class LinkResult:
    """Outcome of linking one podcast or episode."""

    target_id: str
    hosts: List[str] = field(default_factory=list)
    guests: List[str] = field(default_factory=list)
    recurring: List[str] = field(default_factory=list)
    created_entities: List[str] = field(default_factory=list)
    skipped_names: List[str] = field(default_factory=list)
    # Real names that several existing entities share and the bio /
    # description could not tell apart; left unlinked rather than guessed.
    ambiguous_names: List[str] = field(default_factory=list)
    demoted_hosts: List[str] = field(default_factory=list)
    # Guest bullets refused because the voice only read an advert.
    ad_voices: List[str] = field(default_factory=list)
    # Guest bullets refused because the voice was a clip played in the
    # episode: named so by the speaker mapping, or barely speaking.
    clip_voices: List[str] = field(default_factory=list)
    # Names the speaker mapping labels host that are not the show's
    # hosts, taken as participants of the episode (rotating co-hosts).
    cohosts: List[str] = field(default_factory=list)


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
    guest_roles: List[Optional[str]] = []  # the bracket on each guest bullet, aligned with roles.guests
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
            elif heading in _SPEAKER_MAPPING_HEADINGS:
                current_bucket = "speaker_mapping"
            else:
                current_bucket = None
            continue
        if current_bucket is None:
            continue
        if current_bucket == "speaker_mapping":
            map_match = _SPEAKER_MAP_RE.match(line)
            if map_match and map_match.group("role"):
                mapped_name = map_match.group("name").strip()
                mapped_role = map_match.group("role").strip()
                roles.speaker_roles[mapped_name.casefold()] = mapped_role
                if (
                    _HOST_ROLE_RE.search(mapped_role)
                    and not _AD_ROLE_RE.search(mapped_role)
                    and mapped_name.casefold() not in {h.casefold() for h in roles.mapped_hosts}
                ):
                    roles.mapped_hosts.append(mapped_name)
            continue
        bullet_match = _BULLET_RE.match(line)
        if not bullet_match:
            continue
        name, bio, role = _split_bullet(bullet_match.group(1))
        if not name:
            continue
        getattr(roles, current_bucket).append((name, bio))
        if current_bucket == "guests":
            guest_roles.append(role)
    # The speaker mapping may sit above or below Guest(s), so judge the
    # guests once the whole file is read.
    kept: List[Tuple[str, Optional[str]]] = []
    for (name, bio), role in zip(roles.guests, guest_roles):
        speaker_role = roles.speaker_roles.get(name.casefold())
        if _is_ad_voice(role=role, bio=bio, speaker_role=speaker_role):
            roles.ad_voices.append(name)
        elif any(annotation and _CLIP_ROLE_RE.search(annotation) for annotation in (role, speaker_role)):
            roles.clip_voices.append(name)
        else:
            kept.append((name, bio))
    roles.guests = kept
    return roles


def _is_ad_voice(*, role: Optional[str], bio: Optional[str], speaker_role: Optional[str]) -> bool:
    """Whether a guest bullet describes a voice that only read an advert.

    ``role`` is the bracket on the bullet itself, ``speaker_role`` the
    bracket on the same name in the speaker mapping; an ad word in either
    is decisive. ``bio`` is free text and only counts for an ad phrase
    (see ``_AD_BIO_RE``).
    """
    for annotation in (role, speaker_role):
        if annotation and _AD_ROLE_RE.search(annotation):
            return True
    return bool(bio and _AD_BIO_RE.search(bio))


def tally_speakers(markdown: str) -> SpeakerTally:
    """Count each speaker's rows and words in a cleaned Markdown transcript."""
    tally = SpeakerTally()
    for line in markdown.splitlines():
        match = _SPEAKER_ROW_RE.match(line)
        if match:
            label = re.sub(r"\s*\([^)]*\)\s*$", "", match.group("name")).strip()
            if label:
                tally.rows[label.casefold()] += 1
                tally.words[label.casefold()] += len(match.group("text").split())
    return tally


def drop_quiet_guests(
    guests: List[Tuple[str, Optional[str]]],
    speaker_roles: Dict[str, str],
    tally: SpeakerTally,
) -> Tuple[List[Tuple[str, Optional[str]]], List[str]]:
    """Split guests into ``(kept, clip_voices)``: a clip voice has under
    ``CLIP_VOICE_MAX_ROW_SHARE`` of the speaker rows and under
    ``CLIP_VOICE_MAX_WORD_SHARE`` of the words.

    Only names the speaker mapping lists are judged: the cleaner labels
    rows with those names, so one with no rows was never heard as a
    speaker. A Guest(s) name the mapping lacks may simply be spelt
    differently there, so it is kept.
    """
    named = sum(n for label, n in tally.rows.items() if label in speaker_roles)
    if named < CLIP_VOICE_MIN_NAMED_ROWS:
        return list(guests), []
    total_rows = sum(tally.rows.values())
    total_words = sum(tally.words.values())
    kept: List[Tuple[str, Optional[str]]] = []
    quiet: List[str] = []
    for name, bio in guests:
        key = name.casefold()
        if (
            key in speaker_roles
            and tally.rows.get(key, 0) < CLIP_VOICE_MAX_ROW_SHARE * total_rows
            and tally.words.get(key, 0) < CLIP_VOICE_MAX_WORD_SHARE * total_words
        ):
            quiet.append(name)
        else:
            kept.append((name, bio))
    return kept, quiet


def _split_bullet(text: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split a guest bullet into name, bio and the stripped bracket, so
    ``"Evan Gershkovich (Ad Narrator) - Journalist"`` gives
    ``("Evan Gershkovich", "Journalist", "Ad Narrator")``."""
    text = text.strip()
    if not text:
        return "", None, None
    name, bio = text, None
    if " - " in text:
        name, bio = text.split(" - ", 1)
        name = name.strip()
        bio = bio.strip() or None
    role: Optional[str] = None
    role_match = re.search(r"\s*\(([^)]*)\)\s*$", name)
    if role_match:
        role = role_match.group(1).strip() or None
        name = name[: role_match.start()].strip()
    return name, bio, role


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


# Words too common to tell two namesakes apart.
_ROLE_STOPWORDS = frozenset(
    "a an and are as at be by for from has he her his in is it its of on or she that the their they this to was were who with".split()
)


def _tokens(text: Optional[str]) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _context_fit(entity: EntityRecord, name: str, hint: str) -> int:
    """How many distinctive words ``hint`` (the facts-file bio plus the
    podcast or episode description) shares with what is stored about the
    entity: its description and the disambiguator in its canonical name
    ("Scott Galloway (professor)"). The name's own words never count.
    """
    name_tokens = _tokens(name)
    hint_tokens = _tokens(hint) - name_tokens - _ROLE_STOPWORDS
    if not hint_tokens:
        return 0
    known = _tokens(entity.description)
    for parenthetical in re.findall(r"\(([^)]*)\)", entity.canonical_name or ""):
        known |= _tokens(parenthetical)
    return len(hint_tokens & (known - name_tokens))


def _pick_namesake(candidates: List[EntityRecord], name: str, hint: str) -> Optional[EntityRecord]:
    """The one candidate the context fits better than every other, or
    ``None`` when nothing distinguishes them."""
    scored = sorted(((_context_fit(c, name, hint), c.id, c) for c in candidates), key=lambda t: (-t[0], t[1]))
    best, runner_up = scored[0][0], scored[1][0]
    if best == 0 or best == runner_up:
        return None
    return scored[0][2]


def _resolve_existing_person(
    entity_repo: SqliteEntityRepository,
    name: str,
    hint: str,
) -> Tuple[Optional[EntityRecord], bool]:
    """``(entity, ambiguous)`` for a name already in the entity table.

    Names are not unique: Wikidata has a footballer and a professor called
    Scott Galloway, and a corpus can hold a row for each. Picking the first
    match would make whichever row was created first the host of every
    show with that name, and the anchor index would then pre-resolve every
    speaker label to it with no linker in the loop. So when several rows
    share the name, the context has to single one out; otherwise the name
    is reported ambiguous and left unlinked — a missing host costs a few
    unresolved speaker mentions, a wrong one poisons thousands.
    """
    matches = entity_repo.find_entities_by_name(name)
    if not matches:
        return None, False
    if len(matches) == 1:
        return matches[0], False
    chosen = _pick_namesake(matches, name, hint)
    if chosen is None:
        logger.warning(
            "role_name_ambiguous",
            name=name,
            candidates=[{"entity_id": m.id, "qid": m.wikidata_qid} for m in matches],
        )
        return None, True
    logger.info("role_name_disambiguated", name=name, entity_id=chosen.id, candidates=len(matches))
    return chosen, False


def _resolve_or_create_person(
    entity_repo: SqliteEntityRepository,
    name: str,
    bio: Optional[str],
    *,
    context_text: str = "",
) -> Tuple[Optional[str], bool]:
    """Return ``(entity_id, created)``; ``(None, False)`` when the name is
    ambiguous (see ``_resolve_existing_person``).

    A single existing entity by canonical name / alias / id (any type) is
    adopted even if its type is not ``person`` — the existing data is
    authoritative; we don't want to fork a duplicate. Several existing
    entities are told apart by the bio and the description, or refused.
    If nothing matches we mint a new ``person:`` entity seeded with the
    LLM-supplied bio as ``description``.
    """
    hint = " ".join(part for part in (bio, context_text) if part)
    existing, ambiguous = _resolve_existing_person(entity_repo, name, hint)
    if ambiguous:
        return None, False
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
    context_text: str = "",
    resolved_names: Optional[Dict[str, str]] = None,
) -> Tuple[int, dict]:
    """Aggregate ``Name (Host)`` speaker-mapping annotations across a
    podcast's episode facts files.

    Returns ``(files_with_host_annotations, {entity_id: n_files})`` where
    each file counts an entity at most once. ``resolved_names`` maps a
    name to the entity the podcast facts file already settled it to (its
    bio told the namesakes apart); those identities are reused as-is, so
    a name that is ambiguous on the show description alone still counts
    for the host it resolved to — ambiguity here must not read as absence.
    Every other name is resolved read-only via ``_resolve_existing_person``
    — unresolved variants and names shared by several entities contribute
    no evidence; nothing is created here.
    """
    known = {name.casefold(): entity_id for name, entity_id in (resolved_names or {}).items()}
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
                if name.casefold() in known:
                    name_cache[name] = known[name.casefold()]
                else:
                    existing, _ambiguous = _resolve_existing_person(entity_repo, name, context_text)
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
    context_text: str = "",
) -> LinkResult:
    """Parse the podcast facts file and write hosts + recurring.

    ``context_text`` is the podcast description: with the facts-file bio it
    tells namesakes apart (see ``_resolve_existing_person``).
    """
    facts_path = path_manager.podcast_facts_file(podcast_slug)
    roles = parse_facts_file(facts_path)
    result = LinkResult(target_id=podcast_id)
    hosts = _resolve_role_list(
        entity_repo, roles.hosts, accumulator_for_created=result.created_entities, context_text=context_text
    )
    result.hosts, result.created_entities, result.skipped_names, result.ambiguous_names = (
        hosts.entity_ids,
        hosts.created_ids,
        hosts.skipped,
        list(hosts.ambiguous),
    )
    recurring = _resolve_role_list(
        entity_repo, roles.recurring, accumulator_for_created=result.created_entities, context_text=context_text
    )
    result.recurring = recurring.entity_ids
    result.created_entities.extend(recurring.created_ids)
    result.skipped_names.extend(recurring.skipped)
    result.ambiguous_names.extend(recurring.ambiguous)
    if result.hosts:
        evidence_files, host_counts = collect_host_evidence(
            podcast_slug=podcast_slug,
            entity_repo=entity_repo,
            path_manager=path_manager,
            context_text=context_text,
            resolved_names=hosts.resolved_names,
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
    # Write whenever the facts file named someone, including when every
    # name was refused as ambiguous: the stored list may still carry the
    # namesake an earlier run guessed (the footballer as Prof G's host),
    # and that row keeps anchoring speaker mentions until it is cleared.
    # A missing or empty facts file still leaves the columns untouched.
    if hosts.writes:
        entity_repo.set_podcast_hosts(podcast_id, result.hosts)
    if recurring.writes:
        entity_repo.set_podcast_recurring(podcast_id, result.recurring)
    logger.info(
        "podcast_roles_linked",
        podcast_id=podcast_id,
        podcast_slug=podcast_slug,
        hosts=len(result.hosts),
        recurring=len(result.recurring),
        created_entities=len(set(result.created_entities)),
        skipped=len(result.skipped_names),
        ambiguous=result.ambiguous_names,
    )
    return result


def link_episode_roles(
    *,
    episode_id: str,
    podcast_slug: str,
    episode_slug: str,
    entity_repo: SqliteEntityRepository,
    path_manager: PathManager,
    context_text: str = "",
    clean_transcript_path: Optional[str] = None,
    file_storage: Optional[FileStorage] = None,
    clean_transcript_markdown: Optional[str] = None,
    podcast_id: Optional[str] = None,
) -> LinkResult:
    """Parse the episode facts file and write guests.

    ``context_text`` is the episode description (plus the podcast's): with
    the facts-file bio it tells namesakes apart. ``clean_transcript_path``
    is the episode's stored cleaned-transcript path, read through
    ``file_storage`` (it may live in S3); its speaker rows refuse a
    "guest" who was only a clip (``drop_quiet_guests``).
    ``clean_transcript_markdown`` is that transcript already in hand — the
    clean task passes what it just produced, which the configured backend
    may not hold yet — and wins over the path. Without either the
    speaking-share guard is skipped.

    ``podcast_id`` lets the episode's co-hosts in: a name the speaker
    mapping labels host that is not one of the show's linked hosts, and
    that speaks like a participant in the transcript (``_takes_part``),
    joins the guests (``_cohosts``). Without it, without a transcript, or
    while the show has no linked host to tell its own hosts apart, only
    Guest(s) counts.
    """
    facts_path = path_manager.episode_facts_file(podcast_slug, episode_slug)
    roles = parse_facts_file(facts_path)
    candidates = _cohosts(entity_repo, roles, podcast_id, context_text) if podcast_id else []
    markdown = clean_transcript_markdown
    if markdown is None and clean_transcript_path and file_storage is not None and (roles.guests or candidates):
        markdown = _read_clean_transcript(path_manager, file_storage, clean_transcript_path, episode_id)
    tally = tally_speakers(markdown) if markdown else None
    cohosts = [name for name in candidates if tally is not None and _takes_part(name, tally)]
    roles.guests.extend((name, None) for name in cohosts)
    if tally is not None and roles.guests:
        roles.guests, quiet = drop_quiet_guests(roles.guests, roles.speaker_roles, tally)
        roles.clip_voices.extend(quiet)
    result = LinkResult(target_id=episode_id)
    result.cohosts = cohosts
    guests = _resolve_role_list(
        entity_repo, roles.guests, accumulator_for_created=result.created_entities, context_text=context_text
    )
    result.guests, result.created_entities, result.skipped_names, result.ambiguous_names = (
        guests.entity_ids,
        guests.created_ids,
        guests.skipped,
        list(guests.ambiguous),
    )
    result.ad_voices = list(roles.ad_voices)
    result.skipped_names.extend(roles.ad_voices)
    if roles.ad_voices:
        logger.warning(
            "episode_guest_ad_voice_skipped",
            episode_id=episode_id,
            episode_slug=episode_slug,
            names=roles.ad_voices,
        )
    result.clip_voices = list(roles.clip_voices)
    result.skipped_names.extend(roles.clip_voices)
    if roles.clip_voices:
        logger.info(
            "episode_guest_clip_voice_skipped",
            episode_id=episode_id,
            episode_slug=episode_slug,
            names=roles.clip_voices,
        )
    # see link_podcast_roles: an all-ambiguous list clears stale guests. A
    # refused ad or clip voice clears too — an earlier run may have stored it.
    if guests.writes or roles.ad_voices or roles.clip_voices:
        entity_repo.set_episode_guests(episode_id, result.guests)
    logger.info(
        "episode_roles_linked",
        episode_id=episode_id,
        episode_slug=episode_slug,
        guests=len(result.guests),
        created_entities=len(set(result.created_entities)),
        skipped=len(result.skipped_names),
        ambiguous=result.ambiguous_names,
        ad_voices=result.ad_voices,
        clip_voices=result.clip_voices,
        cohosts=result.cohosts,
    )
    return result


def _cohosts(
    entity_repo: SqliteEntityRepository,
    roles: FactsRoles,
    podcast_id: str,
    context_text: str,
) -> List[str]:
    """Speaker-mapping hosts of this episode who are not the show's hosts
    and not already under Guest(s).

    A show with no linked host is skipped: its own host would otherwise
    become a guest of every episode. A name is matched to the show's hosts
    read-only, so nothing is created for a host who is already linked.
    """
    show_hosts = set(entity_repo.get_podcast_anchors(podcast_id).get("hosts", []))
    if not show_hosts:
        return []
    # The show's host may be spelt differently in the mapping ("Neil
    # deGrasse Tyson" against "Neil de Grasse Tyson"); compare names with
    # case, spaces and punctuation ignored, aliases included.
    host_keys = set()
    for host in entity_repo.get_entities_by_ids(list(show_hosts)):
        host_keys |= {_name_key(n) for n in [host.canonical_name, *(host.aliases or [])]}
    listed = {_name_key(name) for name, _bio in roles.guests}
    listed |= {_name_key(name) for name in roles.ad_voices + roles.clip_voices}
    cohosts: List[str] = []
    for name in roles.mapped_hosts:
        key = _name_key(name)
        if (
            key in listed
            or key in host_keys
            or not _is_real_person_name(name)
            or _PLACEHOLDER_NAME_RE.search(name)
            or _AD_ROLE_RE.search(name)
        ):
            continue
        existing, _ambiguous = _resolve_existing_person(entity_repo, name, context_text)
        if existing is not None and existing.id in show_hosts:
            continue
        cohosts.append(name)
    return cohosts


def _takes_part(name: str, tally: SpeakerTally) -> bool:
    """Whether a speaker-mapping co-host speaks like a participant: at
    least ``COHOST_MIN_ROWS`` rows, and not under both clip shares."""
    key = name.casefold()
    rows, words = tally.rows.get(key, 0), tally.words.get(key, 0)
    if rows < COHOST_MIN_ROWS:
        return False
    total_rows, total_words = sum(tally.rows.values()), sum(tally.words.values())
    return rows >= CLIP_VOICE_MAX_ROW_SHARE * total_rows or words >= CLIP_VOICE_MAX_WORD_SHARE * total_words


def _name_key(name: str) -> str:
    """A name with case, spaces and punctuation dropped, and a trailing
    ``(disambiguator)`` too: "Scott Galloway (professor)" → "scottgalloway"."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    return re.sub(r"[\W_]+", "", name.casefold())


def _read_clean_transcript(
    path_manager: PathManager, file_storage: FileStorage, clean_transcript_path: str, episode_id: str
) -> Optional[str]:
    """The cleaned transcript's Markdown, or ``None`` when it cannot be
    read — the guard is then skipped rather than failing the link."""
    key = path_manager.to_relative(path_manager.clean_transcript_file(clean_transcript_path))
    try:
        return file_storage.read_text(key)
    except FileNotFoundError:
        logger.info("episode_roles_transcript_missing", episode_id=episode_id, key=key)
        return None


@dataclass
class _ResolvedRoles:
    """One role list resolved to entity ids.

    ``entity_ids`` is deduplicated while preserving first-occurrence order
    so the resulting JSON column reads in the LLM's preferred order.
    ``ambiguous`` are real names that several existing entities share and
    the context could not settle; they are left unlinked. ``resolved_names``
    maps each linked name to its entity so later read-only lookups (host
    evidence) reuse the identity the bio settled instead of re-deciding on
    weaker context.
    """

    entity_ids: List[str] = field(default_factory=list)
    created_ids: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    ambiguous: List[str] = field(default_factory=list)
    resolved_names: Dict[str, str] = field(default_factory=dict)

    @property
    def writes(self) -> bool:
        """Whether the stored column should be replaced with ``entity_ids``:
        the facts file named at least one real person, even if every one
        was refused as ambiguous (then the column is cleared). Names that
        were skipped as non-persons, or no names at all, leave it alone."""
        return bool(self.entity_ids or self.ambiguous)


def _resolve_role_list(
    entity_repo: SqliteEntityRepository,
    items: Iterable[Tuple[str, Optional[str]]],
    *,
    accumulator_for_created: List[str],
    context_text: str = "",
) -> _ResolvedRoles:
    """Resolve a list of ``(name, bio)`` pairs to entity ids (see ``_ResolvedRoles``)."""
    out = _ResolvedRoles()
    seen_ids: set = set()
    for name, bio in items:
        if not _is_real_person_name(name):
            out.skipped.append(name)
            continue
        entity_id, created = _resolve_or_create_person(entity_repo, name, bio, context_text=context_text)
        if entity_id is None:
            out.ambiguous.append(name)
            continue
        out.resolved_names.setdefault(name, entity_id)
        if entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)
        out.entity_ids.append(entity_id)
        if created:
            out.created_ids.append(entity_id)
    return out


def role_context(podcast, episode=None) -> str:
    """The description text that tells namesakes apart: the episode's, then
    the podcast's. Both are plain text by construction (see ``Episode``)."""
    parts = []
    if episode is not None:
        parts.append(getattr(episode, "description", "") or "")
    parts.append(getattr(podcast, "description", "") or "")
    return " ".join(part for part in parts if part)


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
    ambiguous_names: List[str] = field(default_factory=list)


def backfill_all_roles(
    *,
    podcast_repo: SqlitePodcastRepository,
    entity_repo: SqliteEntityRepository,
    path_manager: PathManager,
    file_storage: Optional[FileStorage] = None,
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
            context_text=podcast.description or "",
        )
        if result.hosts:
            summary.podcasts_with_hosts += 1
        summary.entities_created += len(set(result.created_entities))
        summary.skipped_names.extend(result.skipped_names)
        summary.ambiguous_names.extend(result.ambiguous_names)
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
                context_text=role_context(podcast, episode),
                clean_transcript_path=episode.clean_transcript_path,
                file_storage=file_storage,
                podcast_id=podcast.id,
            )
            if ep_result.guests:
                summary.episodes_with_guests += 1
            summary.entities_created += len(set(ep_result.created_entities))
            summary.skipped_names.extend(ep_result.skipped_names)
            summary.ambiguous_names.extend(ep_result.ambiguous_names)
    return summary
