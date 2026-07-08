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

"""Briefing email delivery (spec #51).

Adds the per-user opt-in flag (``user_briefing_schedules.email_enabled``)
and the ``briefing_deliveries`` table — one row per (briefing, channel),
whose UNIQUE constraint is the send-once anchor.

Same convergence contract as 0002: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (with idempotent ``IF NOT EXISTS`` guards),
so ensure_schema-bootstrapped databases already converge; this migration
exists so Alembic-managed production databases pick the changes up through
``alembic upgrade`` alone.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE user_briefing_schedules
    ADD COLUMN IF NOT EXISTS email_enabled boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS briefing_deliveries (
    id uuid PRIMARY KEY,
    briefing_id uuid NOT NULL REFERENCES user_briefings(id) ON DELETE CASCADE,
    channel text NOT NULL DEFAULT 'email' CHECK (channel IN ('email')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed')),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NULL,
    sent_at timestamptz NULL,
    last_error text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (briefing_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_briefing_deliveries_due
    ON briefing_deliveries(next_attempt_at)
    WHERE status IN ('pending','sending');
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "briefing_deliveries" CASCADE')
    op.execute("ALTER TABLE user_briefing_schedules DROP COLUMN IF EXISTS email_enabled")
