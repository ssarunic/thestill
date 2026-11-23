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

"""Admin curation of the entity graph: merge, split, move mentions, relink,
blacklist and prune.

``thestill entity ...`` and the admin API (``/api/admin/entities``) share
this service, so both apply the same rules and write the same spec #104
audit entries. Callers authorise first: the admin API is mounted behind
``require_admin`` and the CLI is the operator. Every method takes the
acting :class:`Actor`. Nothing here writes SQL; each step is an
``EntityRepository`` operation that both backends implement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from structlog import get_logger

from ..core.entity_alias_hygiene import is_alias_of, lookup_key
from ..core.entity_linking.cache import invalidate_link_decisions
from ..core.entity_linking.shared import _build_entity_id
from ..core.entity_review import CorrectionResult, apply_correction
from ..models.entities import EntityRecord, EntityType
from ..repositories.entity_repository import EntityRepository
from .abuse.actor import Actor
from .abuse.audit_service import AuditLog
from .abuse.errors import AdminActionError

if TYPE_CHECKING:
    from ..repositories.link_decision_repository import LinkDecisionRepository

logger = get_logger(__name__)

_QID_RE = re.compile(r"^Q[1-9][0-9]*$")


@dataclass(frozen=True)
class MergeResult:
    keeper_id: str
    loser_id: str
    #: Rows moved per reference kind (``mentions``, ``overrides``, ``hosts``, ...).
    moved: Dict[str, int]
    #: The loser's name, kept as an alias of the keeper. ``None`` when the
    #: keeper already answers to it, the caller asked to keep nothing, or
    #: the name was refused (``alias_skipped``).
    alias_added: Optional[str] = None
    #: The loser's name when it was not kept because it does not resemble
    #: the keeper's ("Ye" for "Kanye West"). ``clean-aliases`` takes such an
    #: alias for pollution: it would strip it and unlink every mention the
    #: merge just moved. Vouch for it by hand: ``entity-alias-add`` plus a
    #: line in the alias allowlist.
    alias_skipped: Optional[str] = None
    #: The loser's own aliases, deleted with it. They are not carried over:
    #: stored aliases can be wrong (``entity_alias_hygiene``) and an alias
    #: is taken as a name of the keeper by the role linker and the anchors.
    #: Add the right ones by hand with ``thestill entity-alias-add``.
    dropped_aliases: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MoveResult:
    to_entity_id: str
    moved: int
    #: Sources deleted because nothing referred to them any more.
    deleted: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SplitResult:
    entity: EntityRecord
    moved: int
    deleted: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RelinkResult:
    entity_id: str
    old_qid: Optional[str]
    new_qid: Optional[str]
    #: Surface forms now refused for ``old_qid``.
    blacklisted: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SetTypeResult:
    entity_id: str
    old_type: str
    new_type: str


@dataclass(frozen=True)
class PruneResult:
    orphans: List[str]
    deleted: int
    applied: bool


@dataclass(frozen=True)
class RedirectBackfillResult:
    #: ``(loser, keeper)`` in merge order, for merges whose loser is still gone.
    redirects: List[Tuple[str, str]]
    #: Merges skipped because the loser id exists again (a live row wins).
    recreated: List[str]
    applied: bool


class EntityCurationService:
    def __init__(
        self,
        entities: EntityRepository,
        audit: AuditLog,
        link_decisions: Optional["LinkDecisionRepository"] = None,
    ) -> None:
        """``link_decisions`` is the live linker's cache: a blacklisted or
        relinked name is forgotten there so it is decided afresh."""
        self._repo = entities
        self._audit = audit
        self._link_decisions = link_decisions

    def _refuse(self, surface_form: str, wrong_qid: str, reason: Optional[str]) -> None:
        self._repo.add_blacklist_entry(surface_form=surface_form, wrong_qid=wrong_qid, reason=reason)
        invalidate_link_decisions(self._link_decisions, surface_form)

    # -- helpers ---------------------------------------------------------

    def _get(self, entity_id: str) -> EntityRecord:
        entity = self._repo.get_entity(entity_id)
        if entity is None:
            raise AdminActionError("not_found", f"No entity {entity_id!r}.")
        return entity

    @staticmethod
    def _check_qid(qid: Optional[str]) -> Optional[str]:
        if qid is None:
            return None
        qid = qid.strip().upper()
        if not _QID_RE.match(qid):
            raise AdminActionError("invalid_qid", f"{qid!r} is not a Wikidata id like Q42.")
        return qid

    def _qid_free_for(self, qid: Optional[str], entity_id: str) -> None:
        if not qid:
            return
        holder = self._repo.find_entity_by_qid(qid)
        if holder is not None and holder.id != entity_id:
            raise AdminActionError(
                "qid_taken", f"{qid} already belongs to {holder.id}. Merge the two entities instead."
            )

    def _delete_if_unreferenced(self, entity_ids: Sequence[str], *, redirect_to: Optional[str] = None) -> List[str]:
        """Delete each id nothing refers to. ``redirect_to`` is where its
        mentions went, so its public URL answers 301 there."""
        deleted = []
        for entity_id in entity_ids:
            if not self._repo.is_entity_referenced(entity_id) and self._repo.delete_entity(
                entity_id, redirect_to=redirect_to
            ):
                deleted.append(entity_id)
        return deleted

    # -- operations ------------------------------------------------------

    def merge(
        self,
        keeper_id: str,
        loser_id: str,
        *,
        actor: Actor,
        reason: Optional[str] = None,
        dry_run: bool = False,
        keep_name: bool = True,
    ) -> MergeResult:
        """Move everything that refers to ``loser_id`` onto ``keeper_id`` (two
        rows for one thing), then delete the loser. Both must have the same
        type; move mentions or split when they are different things.
        ``dry_run`` checks the rules and writes nothing.

        The loser's name stays as an alias of the keeper: two rows for one
        thing means two names for it, and whatever wrote the loser's name
        will write it again. Without the alias the next ``backfill-roles``
        finds nobody by that name and creates the loser afresh (a show's
        host under a misheard spelling came back this way, and the real host
        became a guest of every episode). Only a name that resembles the
        keeper's is kept (``is_alias_of``: related, or the same name up to
        accents): any other is what ``clean-aliases`` removes, together with
        the mentions under it, so it is reported in ``alias_skipped`` for a
        human to vouch for. ``keep_name=False`` is for a loser whose name is
        not a name of the thing at all.
        """
        if keeper_id == loser_id:
            raise AdminActionError("same_entity", "Keeper and loser are the same entity.")
        keeper = self._repo.get_entity(keeper_id)
        if keeper is None:
            raise AdminActionError("not_found", f"Keeper {keeper_id!r} not found.")
        loser = self._repo.get_entity(loser_id)
        if loser is None:
            raise AdminActionError("not_found", f"Loser {loser_id!r} not found.")
        if keeper.type is not loser.type:
            raise AdminActionError(
                "type_mismatch", f"Cannot merge across types: {keeper.type.value} ≠ {loser.type.value}."
            )
        keeper_names = {lookup_key(name) for name in (keeper.canonical_name, *keeper.aliases)}
        alias = loser.canonical_name.strip()
        alias_added = alias_skipped = None
        if keep_name and alias and lookup_key(alias) not in keeper_names:
            if is_alias_of(alias, keeper.canonical_name):
                alias_added = alias
            else:
                alias_skipped = alias
        dropped = sorted(a for a in loser.aliases if lookup_key(a) not in keeper_names | {lookup_key(alias)})
        if dry_run:
            return MergeResult(keeper.id, loser.id, {}, alias_added, alias_skipped, dropped)
        moved = self._repo.repoint_entity_references(from_entity_id=loser.id, to_entity_id=keeper.id)
        if alias_added:
            # After the references, so a failed repoint leaves name lookups
            # as they were; ``upsert_entity`` unions the alias in.
            self._repo.upsert_entity(keeper.model_copy(update={"aliases": [*keeper.aliases, alias_added]}))
        # The loser's public URL answers 301 to the keeper's from now on.
        self._repo.delete_entity(loser.id, redirect_to=keeper.id)
        self._audit.record(
            actor,
            "entity.merge",
            keeper=keeper.id,
            loser=loser.id,
            moved=moved,
            alias_added=alias_added,
            alias_skipped=alias_skipped,
            dropped_aliases=dropped,
            reason=reason,
        )
        return MergeResult(keeper.id, loser.id, moved, alias_added, alias_skipped, dropped)

    def move_mentions(
        self,
        mention_ids: Sequence[int],
        *,
        from_entity_id: str,
        to_entity_id: str,
        actor: Actor,
        delete_emptied: bool = False,
        reason: Optional[str] = None,
    ) -> MoveResult:
        """Point chosen mentions of one entity at another: one name, two
        things (``Islam`` the religion and Islam Makhachev). Only mentions
        that currently point at ``from_entity_id`` move."""
        if from_entity_id == to_entity_id:
            raise AdminActionError("same_entity", "Source and target are the same entity.")
        if not mention_ids:
            raise AdminActionError("no_mentions", "Name at least one mention to move.")
        self._get(from_entity_id)
        self._get(to_entity_id)
        moved = self._repo.move_mentions(mention_ids, from_entity_ids=[from_entity_id], to_entity_id=to_entity_id)
        deleted = self._delete_if_unreferenced([from_entity_id], redirect_to=to_entity_id) if delete_emptied else []
        self._audit.record(
            actor,
            "entity.move_mentions",
            from_entity_id=from_entity_id,
            to_entity_id=to_entity_id,
            mention_ids=sorted(mention_ids),
            moved=moved,
            deleted=deleted,
            reason=reason,
        )
        return MoveResult(to_entity_id, moved, deleted)

    def split(
        self,
        *,
        name: str,
        entity_type: EntityType,
        from_entity_id: str,
        actor: Actor,
        qid: Optional[str] = None,
        description: Optional[str] = None,
        entity_id: Optional[str] = None,
        mention_ids: Optional[Sequence[int]] = None,
        delete_emptied: bool = True,
        reason: Optional[str] = None,
    ) -> SplitResult:
        """Create a new entity and move mentions of ``from_entity_id`` to it:
        the chosen ``mention_ids``, or every reference when ``None``. The
        source is deleted once nothing refers to it (``delete_emptied``)."""
        qid = self._check_qid(qid)
        source = self._get(from_entity_id)
        new_id = entity_id or _build_entity_id(entity_type, name, qid)
        if self._repo.get_entity(new_id) is not None:
            raise AdminActionError("exists", f"{new_id} already exists. Move mentions to it instead.")
        self._qid_free_for(qid, new_id)
        entity = EntityRecord(
            id=new_id, type=entity_type, canonical_name=name, wikidata_qid=qid, aliases=[], description=description
        )
        self._repo.upsert_entity(entity)
        if mention_ids is None:
            moved = self._repo.repoint_entity_references(from_entity_id=source.id, to_entity_id=new_id)["mentions"]
        else:
            moved = self._repo.move_mentions(mention_ids, from_entity_ids=[source.id], to_entity_id=new_id)
        deleted = self._delete_if_unreferenced([source.id], redirect_to=new_id) if delete_emptied else []
        self._audit.record(
            actor,
            "entity.split",
            entity_id=new_id,
            from_entity_id=source.id,
            qid=qid,
            mention_ids=sorted(mention_ids) if mention_ids is not None else "all",
            moved=moved,
            deleted=deleted,
            reason=reason,
        )
        return SplitResult(self._get(new_id), moved, deleted)

    def relink(
        self,
        entity_id: str,
        qid: Optional[str],
        *,
        actor: Actor,
        description: Optional[str] = None,
        blacklist_old: bool = True,
        reason: Optional[str] = None,
    ) -> RelinkResult:
        """Point an entity at the right Wikidata item (``None`` unlinks it).
        The old description, P31 classes, image and facts came from the
        wrong item and are dropped (``enrich-entities`` fetches new ones).
        With ``blacklist_old`` the entity's names are refused for the old
        QID so the linker cannot pick it again."""
        qid = self._check_qid(qid)
        entity = self._get(entity_id)
        old = entity.wikidata_qid
        if old == qid:
            raise AdminActionError("unchanged", f"{entity.id} is already linked to {qid}.")
        self._qid_free_for(qid, entity.id)
        self._repo.set_entity_wikidata(entity.id, qid=qid, description=description, instance_of=[])
        self._repo.delete_enrichment(entity.id)
        blacklisted: List[str] = []
        if blacklist_old and old:
            note = reason or f"{entity.id} relinked {old} → {qid}"
            for surface in sorted({entity.canonical_name, *entity.aliases}):
                self._refuse(surface, old, note)
                blacklisted.append(surface)
        self._audit.record(
            actor, "entity.relink", entity_id=entity.id, old=old, new=qid, blacklisted=blacklisted, reason=reason
        )
        return RelinkResult(entity.id, old, qid, blacklisted)

    def set_type(
        self, entity_id: str, entity_type: EntityType, *, actor: Actor, reason: Optional[str] = None
    ) -> SetTypeResult:
        """Move an entity to another bucket in place: id, mentions, aliases and
        link stay, and the old-type URL redirects (#417). A row still linked to
        Wikidata can be moved back by the resolver or the next
        ``backfill-entity-types``; when the link is the mistake, unlink it too."""
        entity = self._get(entity_id)
        if entity.type is entity_type:
            raise AdminActionError("unchanged", f"{entity.id} is already a {entity_type.value}.")
        self._repo.upsert_entity(entity.model_copy(update={"type": entity_type}))
        self._audit.record(
            actor, "entity.set_type", entity_id=entity.id, old=entity.type.value, new=entity_type.value, reason=reason
        )
        return SetTypeResult(entity.id, entity.type.value, entity_type.value)

    def blacklist(self, surface_form: str, wrong_qid: str, *, actor: Actor, reason: Optional[str] = None) -> None:
        """Refuse ``surface_form → wrong_qid`` in every future resolution."""
        wrong_qid = self._check_qid(wrong_qid) or ""
        if not surface_form.strip():
            raise AdminActionError("no_surface", "Give the name to refuse.")
        self._refuse(surface_form, wrong_qid, reason)
        self._audit.record(actor, "entity.blacklist", surface_form=surface_form, wrong_qid=wrong_qid, reason=reason)

    def prune_orphans(self, *, actor: Actor, apply: bool = False) -> PruneResult:
        """List entity rows nothing refers to; with ``apply``, delete them
        (re-checked per batch so a row that gained a mention stays)."""
        orphans = self._repo.find_orphan_entity_ids()
        if not apply:
            return PruneResult(orphans, 0, applied=False)
        deleted = self._repo.delete_orphan_entities(orphans)
        self._audit.record(actor, "entity.prune_orphans", listed=len(orphans), deleted=deleted)
        return PruneResult(orphans, deleted, applied=True)

    def backfill_merge_redirects(self, *, actor: Actor, apply: bool = False) -> RedirectBackfillResult:
        """Redirects for merges made before ``entity_redirects`` existed,
        read from the ``entity.merge`` audit entries, oldest first so a
        chain (A into B, later B into C) ends at C. A merge whose loser id
        exists again is skipped: a live row wins over a redirect. Only
        audited merges can be recovered; the older QID and fuzzy dedupes
        wrote no audit row. Reports by default; ``apply`` writes."""
        entries = []
        page = 500
        while True:
            batch = self._audit.entries(action="entity.merge", limit=page, offset=len(entries))
            entries.extend(batch)
            if len(batch) < page:
                break
        redirects: List[Tuple[str, str]] = []
        recreated: List[str] = []
        for entry in sorted(entries, key=lambda e: (e.created_at, e.id or 0)):
            keeper, loser = entry.details.get("keeper"), entry.details.get("loser")
            if not keeper or not loser:
                continue
            if self._repo.get_entity(str(loser)) is not None:
                recreated.append(str(loser))
                continue
            redirects.append((str(loser), str(keeper)))
        if not apply:
            return RedirectBackfillResult(redirects, recreated, applied=False)
        for loser, keeper in redirects:
            self._repo.record_entity_redirect(loser, keeper)
        self._audit.record(actor, "entity.backfill_redirects", recorded=len(redirects), skipped=len(recreated))
        return RedirectBackfillResult(redirects, recreated, applied=True)

    # -- per-item curation (the CLI ``mention`` / ``podcast`` / ``episode``
    # commands and the admin correction endpoint). Each write is audited
    # here, so no entry point can skip it.

    def _mention(self, mention_id: int):
        mention = self._repo.get_mention(mention_id)
        if mention is None:
            raise AdminActionError("not_found", f"No mention {mention_id}.")
        return mention

    def _require_entities(self, entity_ids: Sequence[str]) -> List[str]:
        ids = list(entity_ids)
        missing = [eid for eid in ids if self._repo.get_entity(eid) is None]
        if missing:
            raise AdminActionError("not_found", f"Unknown entity ids: {', '.join(missing)}.")
        return ids

    def drop_mention(self, mention_id: int, *, actor: Actor, reason: Optional[str] = None, scope_episode: bool = False):
        """Soft-delete a mention and store a ``drop`` override so a reindex
        keeps it dropped. The override is global by surface form unless
        ``scope_episode``."""
        mention = self._mention(mention_id)
        self._repo.add_override(
            surface_form=mention.surface_form,
            episode_id=mention.episode_id if scope_episode else None,
            kind="drop",
            reason=reason,
        )
        invalidate_link_decisions(self._link_decisions, mention.surface_form)
        self._repo.resolve_mention(mention_id=mention_id, entity_id=None, status="dropped", method="override")
        self._audit.record(
            actor,
            "mention.drop",
            mention_id=mention_id,
            surface_form=mention.surface_form,
            episode_id=mention.episode_id,
            scope="episode" if scope_episode else "global",
            reason=reason,
        )
        return mention

    def repoint_mention(
        self,
        mention_id: int,
        entity_id: str,
        *,
        actor: Actor,
        reason: Optional[str] = None,
        scope_global: bool = False,
    ):
        """Force a mention to resolve to ``entity_id`` and store a
        ``force_entity`` override (per episode unless ``scope_global``).

        Returns ``(mention, target_entity)``."""
        mention = self._mention(mention_id)
        target = self._get(entity_id)
        self._repo.add_override(
            surface_form=mention.surface_form,
            episode_id=None if scope_global else mention.episode_id,
            kind="force_entity",
            entity_id=entity_id,
            reason=reason,
        )
        invalidate_link_decisions(self._link_decisions, mention.surface_form)
        self._repo.resolve_mention(mention_id=mention_id, entity_id=entity_id, status="resolved", method="override")
        self._audit.record(
            actor,
            "mention.repoint",
            mention_id=mention_id,
            surface_form=mention.surface_form,
            episode_id=mention.episode_id,
            from_entity_id=mention.entity_id,
            to_entity_id=entity_id,
            scope="global" if scope_global else "episode",
            reason=reason,
        )
        return mention, target

    def add_alias(self, entity_id: str, alias: str, *, actor: Actor) -> bool:
        """Add ``alias`` to the entity's aliases. Returns False (and audits
        nothing) when it is already there."""
        entity = self._get(entity_id)
        alias = alias.strip()
        if not alias:
            raise AdminActionError("no_surface", "Give the alias to add.")
        if alias in entity.aliases:
            return False
        entity.aliases = sorted(set(entity.aliases) | {alias})
        self._repo.upsert_entity(entity)
        self._audit.record(actor, "entity.alias_add", entity_id=entity_id, alias=alias)
        return True

    def set_visibility(
        self, entity_id: str, *, public: bool, actor: Actor, reason: Optional[str] = None
    ) -> EntityRecord:
        """Show or hide the entity's public page (spec #94 takedown switch)."""
        entity = self._get(entity_id)
        self._repo.set_entity_public_hidden(entity.id, not public)
        self._audit.record(actor, "entity.set_visibility", entity_id=entity.id, public=public, reason=reason)
        return entity

    def pin_hosts(self, podcast_id: str, entity_ids: Sequence[str], *, actor: Actor) -> List[str]:
        """Pin a podcast's canonical hosts (the extractor's anchor pass)."""
        ids = self._require_entities(entity_ids)
        old = self._repo.get_podcast_anchors(podcast_id).get("hosts", [])
        self._repo.set_podcast_hosts(podcast_id, ids)
        self._audit.record(actor, "podcast.set_hosts", podcast_id=podcast_id, old=old, new=ids)
        return ids

    def pin_recurring(self, podcast_id: str, entity_ids: Sequence[str], *, actor: Actor) -> List[str]:
        """Pin a podcast's recurring voices (regular co-hosts)."""
        ids = self._require_entities(entity_ids)
        old = self._repo.get_podcast_anchors(podcast_id).get("recurring", [])
        self._repo.set_podcast_recurring(podcast_id, ids)
        self._audit.record(actor, "podcast.set_recurring", podcast_id=podcast_id, old=old, new=ids)
        return ids

    def pin_guests(self, episode_id: str, entity_ids: Sequence[str], *, actor: Actor) -> List[str]:
        """Pin an episode's guests."""
        ids = self._require_entities(entity_ids)
        old = self._repo.get_episode_guests(episode_id)
        self._repo.set_episode_guests(episode_id, ids)
        self._audit.record(actor, "episode.set_guests", episode_id=episode_id, old=old, new=ids)
        return ids

    def correct(self, *, actor: Actor, queue_manager, wikidata_client=None, **correction) -> CorrectionResult:
        """Apply an admin correction (blacklist / override) and queue the
        re-resolution (``core.entity_review.apply_correction``), audited.

        ``CorrectionError`` and ``EnrichmentUnavailable`` propagate; nothing
        is audited when the correction is refused."""
        result = apply_correction(
            repo=self._repo,
            queue_manager=queue_manager,
            wikidata_client=wikidata_client,
            link_decisions=self._link_decisions,
            **correction,
        )
        self._audit.record(
            actor,
            "entity.correction",
            correction=result.action,
            surface_form=result.surface_form,
            episode_id=correction.get("episode_id"),
            wrong_qid=correction.get("wrong_qid"),
            target_entity_id=result.target_entity_id,
            created_entity_id=result.created_entity_id,
            affected_mentions=result.affected_mentions,
            reason=correction.get("reason"),
        )
        return result
