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

"""Briefing schedules (spec #50).

``user_briefing_schedules`` also lives in ``postgres_schema.SCHEMA_SQL``, so
databases bootstrapped via ``ensure_schema`` already have it; the DDL here is
``IF NOT EXISTS`` and converges (FM-6, same pattern as 0001). This migration
exists so Alembic-managed production databases stamped at 0001 pick the table
up through ``alembic upgrade`` alone.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS user_briefing_schedules (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    frequency text NOT NULL DEFAULT 'daily' CHECK (frequency IN ('daily','weekly')),
    hour_local bigint NOT NULL DEFAULT 8 CHECK (hour_local BETWEEN 0 AND 23),
    weekday bigint NULL CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
    timezone text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    next_run_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((frequency = 'weekly') = (weekday IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_briefing_schedules_due
    ON user_briefing_schedules(next_run_at)
    WHERE enabled AND next_run_at IS NOT NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "user_briefing_schedules" CASCADE')
