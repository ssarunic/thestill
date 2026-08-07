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

"""Performance indices (spec #69 Phase 1).

EXPLAIN-verified fixes from the 2026-08-06 performance review:

- ``idx_episodes_pub_date`` is rebuilt as ``idx_episodes_pub_date_nulls_last``
  (``pub_date DESC NULLS LAST``): every hot listing orders ``DESC NULLS
  LAST``, which the default ``DESC`` (= ``NULLS FIRST``) index cannot serve —
  measured as a full seq-scan + sort on the main episode listing.
- ``idx_inbox_user_all(user_id, delivered_at DESC)``: the default inbox view
  filters ``state != 'dismissed'`` and sorts by ``delivered_at``; the ``!=``
  breaks ``idx_inbox_user_state``'s middle column. Restores the shape the
  SQLite schema always had. Plus ``idx_inbox_user_source`` for the
  import rate-limit count.
- FK / reverse-side indices Postgres does not create automatically:
  ``tasks.podcast_id``, ``episode_related.related_episode_id``,
  ``entity_cooccurrences.entity_b_id``, ``mention_overrides.episode_id`` /
  ``entity_id`` — cascade deletes and ``OR entity_b_id`` query legs
  currently seq-scan.
- Pipeline-state partials completing the existing four-stage set: CLEANED
  (summarize-stage poll) and failed (DLQ view, measured seq-scan).
- Text-lookup indices: pg_trgm GIN on ``episodes.title`` and
  ``entities.canonical_name`` (``ILIKE '%term%'`` searches),
  ``LOWER(surface_form)`` expression indices on ``entity_mentions`` /
  ``mention_overrides`` / ``resolution_blacklist`` (per-mention resolution
  path), ``LOWER(canonical_name)`` on entities, and a ``jsonb_path_ops`` GIN
  on ``episodes.guest_entity_ids`` for the ``@>`` containment probe.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent), so ensure_schema-bootstrapped
databases converge on fresh installs; this migration exists so
Alembic-managed production databases pick the change up through
``alembic upgrade`` alone (including the drop of the superseded
``idx_episodes_pub_date``, which ``CREATE INDEX IF NOT EXISTS`` cannot do).

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_DDL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1.1 pub_date sort order
DROP INDEX IF EXISTS idx_episodes_pub_date;
CREATE INDEX IF NOT EXISTS idx_episodes_pub_date_nulls_last
    ON episodes(pub_date DESC NULLS LAST);

-- 1.2 inbox
CREATE INDEX IF NOT EXISTS idx_inbox_user_all
    ON user_episode_inbox(user_id, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_user_source
    ON user_episode_inbox(user_id, source, delivered_at);

-- 1.3 FK / reverse-side indices
CREATE INDEX IF NOT EXISTS idx_tasks_podcast_stage
    ON tasks(podcast_id, stage) WHERE podcast_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episode_related_target
    ON episode_related(related_episode_id);
CREATE INDEX IF NOT EXISTS idx_cooccur_entity_b
    ON entity_cooccurrences(entity_b_id);
CREATE INDEX IF NOT EXISTS idx_overrides_episode
    ON mention_overrides(episode_id) WHERE episode_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_overrides_entity
    ON mention_overrides(entity_id) WHERE entity_id IS NOT NULL;

-- 1.4 pipeline-state partials
CREATE INDEX IF NOT EXISTS idx_episodes_state_cleaned
    ON episodes(podcast_id, pub_date DESC)
    WHERE clean_transcript_path IS NOT NULL AND summary_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_failed
    ON episodes(failed_at DESC) WHERE failed_at_stage IS NOT NULL;

-- 1.5 text lookups
CREATE INDEX IF NOT EXISTS idx_episodes_title_trgm
    ON episodes USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_episodes_guest_entities
    ON episodes USING gin (guest_entity_ids jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_entities_name_trgm
    ON entities USING gin (canonical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_entities_name_lower
    ON entities(LOWER(canonical_name));
CREATE INDEX IF NOT EXISTS idx_mentions_surface_lower
    ON entity_mentions(LOWER(surface_form));
CREATE INDEX IF NOT EXISTS idx_overrides_surface_lower
    ON mention_overrides(LOWER(surface_form));
CREATE INDEX IF NOT EXISTS idx_blacklist_surface_lower
    ON resolution_blacklist(LOWER(surface_form));
"""

_DOWN_DDL = """
DROP INDEX IF EXISTS idx_blacklist_surface_lower;
DROP INDEX IF EXISTS idx_overrides_surface_lower;
DROP INDEX IF EXISTS idx_mentions_surface_lower;
DROP INDEX IF EXISTS idx_entities_name_lower;
DROP INDEX IF EXISTS idx_entities_name_trgm;
DROP INDEX IF EXISTS idx_episodes_guest_entities;
DROP INDEX IF EXISTS idx_episodes_title_trgm;
DROP INDEX IF EXISTS idx_episodes_failed;
DROP INDEX IF EXISTS idx_episodes_state_cleaned;
DROP INDEX IF EXISTS idx_overrides_entity;
DROP INDEX IF EXISTS idx_overrides_episode;
DROP INDEX IF EXISTS idx_cooccur_entity_b;
DROP INDEX IF EXISTS idx_episode_related_target;
DROP INDEX IF EXISTS idx_tasks_podcast_stage;
DROP INDEX IF EXISTS idx_inbox_user_source;
DROP INDEX IF EXISTS idx_inbox_user_all;
DROP INDEX IF EXISTS idx_episodes_pub_date_nulls_last;
CREATE INDEX IF NOT EXISTS idx_episodes_pub_date ON episodes(pub_date DESC);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute(_DOWN_DDL)
