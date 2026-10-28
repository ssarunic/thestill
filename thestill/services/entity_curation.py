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
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from structlog import get_logger

from ..core.entity_linking.cache import invalidate_link_decisions
from ..core.entity_linking.shared import _build_entity_id
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

    def _delete_if_unreferenced(self, entity_ids: Sequence[str]) -> List[str]:
        deleted = []
        for entity_id in entity_ids:
            if not self._repo.is_entity_referenced(entity_id) and self._repo.delete_entity(entity_id):
                deleted.append(entity_id)
        return deleted

    # -- operations ------------------------------------------------------

    def merge(
        self, keeper_id: str, loser_id: str, *, actor: Actor, reason: Optional[str] = None, dry_run: bool = False
    ) -> MergeResult:
        """Move everything that refers to ``loser_id`` onto ``keeper_id`` (two
        rows for one thing), then delete the loser. Both must have the same
        type; move mentions or split when they are different things.
        ``dry_run`` checks the rules and writes nothing."""
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
        if dry_run:
            return MergeResult(keeper.id, loser.id, {})
        moved = self._repo.repoint_entity_references(from_entity_id=loser.id, to_entity_id=keeper.id)
        self._repo.delete_entity(loser.id)
        self._audit.record(actor, "entity.merge", keeper=keeper.id, loser=loser.id, moved=moved, reason=reason)
        return MergeResult(keeper.id, loser.id, moved)

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
        deleted = self._delete_if_unreferenced([from_entity_id]) if delete_emptied else []
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
        deleted = self._delete_if_unreferenced([source.id]) if delete_emptied else []
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
