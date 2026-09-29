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

"""Per-episode platform links (spec #87).

``episode_platform_links`` holds one row per (episode, platform): the
episode's page on Apple Podcasts / Spotify / YouTube, or a ``url IS NULL``
"checked, not found" marker whose ``checked_at`` throttles re-lookups.
``podcasts.spotify_url`` is the publisher-provided Spotify show link (never
chart-sourced, unlike ``apple_url`` / ``youtube_url``).

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent), so ensure_schema-bootstrapped
databases already converge; this migration exists so Alembic-managed
production databases pick the table up through ``alembic upgrade`` alone.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS episode_platform_links (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    platform text NOT NULL CHECK (platform IN ('apple', 'spotify', 'youtube')),
    url text NULL,
    external_ref text NULL,
    match_method text NULL CHECK (match_method IN ('guid', 'audio_url', 'title_date', 'title_duration', 'publisher')),
    checked_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(episode_id, platform)
);
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS spotify_url text NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "episode_platform_links" CASCADE')
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS spotify_url")
