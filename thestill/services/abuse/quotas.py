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

"""Spec #104 §2.2 — the per-user quotas, declared once.

Each quota's ledger kind, window, lock group, env default and refusal text
live here; the settings registry, the error messages and the service all
read this table.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional

from ...repositories.usage_event_repository import FOLLOW_LOCK_GROUP

DAY = timedelta(hours=24)


@dataclass(frozen=True)
class QuotaSpec:
    name: str
    #: Ledger kind counted; None for ``follows_total`` (live follows).
    kind: Optional[str]
    #: Rolling window; None for a limit no wait lifts.
    window: Optional[timedelta]
    #: Quotas in one group share a lock. The follow group is checked inside
    #: ``follow_with_gate``, never through ``QuotaService.reserve``.
    lock_group: str
    #: ``Config`` field holding the env default.
    config_attr: str
    #: Refusal text; ``{wait}`` reads "6 hours" / "45 minutes".
    message: str


QUOTAS: Dict[str, QuotaSpec] = {
    spec.name: spec
    for spec in (
        QuotaSpec(
            "feeds_introduced_per_day",
            "feed_introduced",
            DAY,
            FOLLOW_LOCK_GROUP,
            "quota_feeds_introduced_per_day",
            "You've added {limit} new podcasts today. You can add more in {wait}.",
        ),
        QuotaSpec(
            "follows_total",
            None,
            None,
            FOLLOW_LOCK_GROUP,
            "quota_follows_total",
            "You follow {limit} podcasts, the most allowed. Unfollow one to follow another.",
        ),
        QuotaSpec(
            "follows_per_day",
            "follow",
            DAY,
            FOLLOW_LOCK_GROUP,
            "quota_follows_per_day",
            "You've followed {limit} podcasts today. You can follow more in {wait}.",
        ),
        QuotaSpec(
            "imports_per_day",
            "import",
            DAY,
            "import",
            "quota_imports_per_day",
            "You've imported {limit} episodes today. You can import more in {wait}.",
        ),
        QuotaSpec(
            "translations_per_day",
            "translation",
            DAY,
            "translation",
            "quota_translations_per_day",
            "You've translated {limit} summaries today. You can translate more in {wait}.",
        ),
        QuotaSpec(
            "narrations_per_day",
            "narration",
            DAY,
            "narration",
            "quota_narrations_per_day",
            "You've narrated {limit} briefings today. You can narrate more in {wait}.",
        ),
    )
}

QUOTA_NAMES = tuple(QUOTAS)
