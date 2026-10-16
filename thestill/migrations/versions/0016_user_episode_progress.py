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

"""Per-user listening progress (spec #90).

``user_episode_progress`` also lives in ``postgres_schema.SCHEMA_SQL``, so
databases bootstrapped via ``ensure_schema`` already have it; the DDL here
is ``IF NOT EXISTS`` and converges (same contract as 0002). This migration
exists so Alembic-managed production databases pick the table up through
``alembic upgrade`` alone.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS user_episode_progress (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    position_seconds double precision NOT NULL CHECK (position_seconds >= 0),
    duration_seconds double precision NULL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    completed_at timestamptz NULL,
    stopped_at timestamptz NULL,
    session_id text NOT NULL,
    seq bigint NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_user_updated ON user_episode_progress(user_id, updated_at DESC);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "user_episode_progress" CASCADE')
