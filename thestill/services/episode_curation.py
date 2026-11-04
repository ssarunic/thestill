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

"""Admin repair of episode rows: fold import twins into the import.

An import twin is two rows for one episode in one show. The import missed
the feed's existing row, because the host had moved the file since Apple
indexed it, so it inserted its own row and processed that. The feed row
stayed unprocessed, and because both share a slug, the episode page served
the unprocessed one (GDIY #570, 2026-11-04). ``ImportService`` now matches
on the feed GUID; this repairs the twins created before that.

``thestill episode fold-import-twins`` is the caller (the CLI is the
operator). Nothing here writes SQL: the repository finds candidates and
folds one pair atomically, and every fold goes in the spec #104 audit log.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, List, Optional

from structlog import get_logger

from ..utils.datetime_utils import ensure_utc

if TYPE_CHECKING:
    from .abuse.actor import Actor
    from .abuse.audit_service import AuditLog

logger = get_logger(__name__)

#: How far apart the two rows' publish dates may be. Twins carry the same
#: date; a rerun that shares the slug ("Best of: …") is weeks or years off.
PUB_DATE_TOLERANCE = timedelta(days=1)


@dataclass(frozen=True)
class ImportTwin:
    keeper_id: str  # the import's row: carries the canonical id, usually processed
    twin_id: str  # the untouched feed row
    podcast_slug: str
    episode_slug: str
    twin_external_id: str
    twin_audio_url: Optional[str]


@dataclass(frozen=True)
class FoldResult:
    twins: List[ImportTwin]
    folded: int
    applied: bool


class EpisodeCurationService:
    def __init__(self, repository: Any, audit: "AuditLog") -> None:
        self._repo = repository
        self._audit = audit

    def find_import_twins(self, podcast_id: Optional[str] = None) -> List[ImportTwin]:
        """Twin pairs whose publish dates agree. A row that pairs more than
        once is left out: folding needs exactly one keeper and one twin."""
        rows = [
            row
            for row in self._repo.find_import_twin_candidates(podcast_id)
            if _same_day(row["keeper_pub_date"], row["twin_pub_date"])
        ]
        uses = Counter([row["keeper_id"] for row in rows] + [row["twin_id"] for row in rows])
        twins = []
        for row in rows:
            if uses[row["keeper_id"]] > 1 or uses[row["twin_id"]] > 1:
                logger.warning("import_twin_ambiguous", keeper_id=row["keeper_id"], twin_id=row["twin_id"])
                continue
            twins.append(
                ImportTwin(
                    keeper_id=row["keeper_id"],
                    twin_id=row["twin_id"],
                    podcast_slug=row["podcast_slug"],
                    episode_slug=row["episode_slug"],
                    twin_external_id=row["twin_external_id"],
                    twin_audio_url=row["twin_audio_url"],
                )
            )
        return twins

    def fold_import_twins(self, *, actor: "Actor", apply: bool = False, podcast_id: Optional[str] = None) -> FoldResult:
        """List import twins; with ``apply``, fold each twin into its keeper.

        A pair whose twin was touched since the listing (a task, a processed
        file) is skipped by the repository and not counted.
        """
        twins = self.find_import_twins(podcast_id)
        if not apply:
            return FoldResult(twins, 0, applied=False)
        folded = 0
        for twin in twins:
            if not self._repo.fold_import_twin(twin.keeper_id, twin.twin_id):
                continue
            folded += 1
            self._audit.record(
                actor,
                "episode.fold_import_twin",
                keeper_id=twin.keeper_id,
                twin_id=twin.twin_id,
                episode=f"{twin.podcast_slug}/{twin.episode_slug}",
                twin_external_id=twin.twin_external_id,
                twin_audio_url=twin.twin_audio_url,
            )
        return FoldResult(twins, folded, applied=True)


def _same_day(a: Any, b: Any) -> bool:
    first, second = _as_datetime(a), _as_datetime(b)
    if first is None or second is None:
        return False
    return abs(first - second) <= PUB_DATE_TOLERANCE


def _as_datetime(value: Any) -> Optional[datetime]:
    """SQLite returns ISO strings, Postgres ``datetime``s."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return ensure_utc(value) if isinstance(value, datetime) else None


__all__ = ["EpisodeCurationService", "FoldResult", "ImportTwin", "PUB_DATE_TOLERANCE"]
