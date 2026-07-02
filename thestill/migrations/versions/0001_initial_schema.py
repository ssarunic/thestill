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

"""Initial typed schema (spec #44).

Executes ``postgres_schema.SCHEMA_SQL`` — the single source of truth shared
with the ``ensure_schema`` dev/test bootstrap — so both paths converge on an
identical schema and cannot drift (FM-6). All DDL is ``IF NOT EXISTS``, so
upgrading a database that was bootstrapped via ``ensure_schema`` is a no-op
plus a version stamp.

The pgvector dimensionality is resolved from ``EMBEDDING_MODEL`` (same env
var the app reads) via the model registry.

Revision ID: 0001
Revises:
Create Date: 2026-07-02
"""

from __future__ import annotations

import os

from alembic import op

from thestill.repositories.postgres_schema import SCHEMA_SQL
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Reverse-FK order for downgrade.
_TABLES = [
    "episode_related",
    "related_idf",
    "episode_vectors",
    "chunks",
    "resolution_blacklist",
    "mention_overrides",
    "entity_enrichment",
    "entity_cooccurrences",
    "entity_mentions",
    "entities",
    "pending_transcription_operations",
    "tasks",
    "user_briefings",
    "briefing_episodes",
    "briefings",
    "digest_episodes",
    "digests",
    "user_episode_inbox",
    "podcast_followers",
    "episode_transcript_links",
    "episode_alternate_enclosures",
    "episodes",
    "podcasts",
    "top_podcast_rankings",
    "top_podcasts",
    "top_podcasts_meta",
    "categories",
    "revoked_tokens",
    "users",
]


def upgrade() -> None:
    dim = embedding_dim_for(os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL)
    op.execute(SCHEMA_SQL.format(dim=dim))


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
