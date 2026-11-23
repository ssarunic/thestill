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

"""Sync tracks (spec #116).

``episode_sync_tracks`` indexes the per-episode sync track files: which
file describes the episode's current raw transcript, and which was built
from audio whose transcript is not stored yet (``pending``). One of each
per episode at most. The files themselves live in file storage.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS episode_sync_tracks (
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    generation text NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'current')),
    path text NOT NULL,
    source text NOT NULL,
    frames integer NOT NULL,
    anchors integer NOT NULL,
    audio_bytes bigint NOT NULL,
    audio_duration_s double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (episode_id, generation)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_tracks_one_per_state ON episode_sync_tracks(episode_id, state);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS episode_sync_tracks CASCADE")
