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

"""Refresh failure classification (spec #60): durable per-kind policy state.

Adds the ``podcasts`` columns backing the failure-kind taxonomy — what kind
of failure last hit a feed (``last_refresh_failure_kind`` /
``last_refresh_status_code``), how long the current failure streak has run
(``consecutive_refresh_failures`` / ``refresh_failure_streak_started_at``),
why a feed is quarantined (``refresh_disabled_reason``; NULL = active), and
any server-directed retry time (``refresh_retry_after_at``).

All columns are nullable / zero-default: NULL is exactly "no classified
failure yet", so no backfill is needed. Legacy parked rows (generic
``last_refresh_error``, no reason) surface as reason ``unknown`` in the
health counts until they fail again under the new classifier.

Same convergence contract as the earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent), so ensure_schema-bootstrapped
databases already converge; this migration exists so Alembic-managed
production databases pick the change up through ``alembic upgrade`` alone.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE podcasts
    ADD COLUMN IF NOT EXISTS last_refresh_failure_kind text NULL,
    ADD COLUMN IF NOT EXISTS last_refresh_status_code integer NULL,
    ADD COLUMN IF NOT EXISTS consecutive_refresh_failures integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refresh_failure_streak_started_at timestamptz NULL,
    ADD COLUMN IF NOT EXISTS refresh_disabled_reason text NULL,
    ADD COLUMN IF NOT EXISTS refresh_retry_after_at timestamptz NULL;

CREATE INDEX IF NOT EXISTS idx_podcasts_quarantine
    ON podcasts(refresh_disabled_reason, last_refresh_at)
    WHERE refresh_disabled_reason IS NOT NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_podcasts_quarantine")
    op.execute(
        "ALTER TABLE podcasts "
        "DROP COLUMN IF EXISTS last_refresh_failure_kind, "
        "DROP COLUMN IF EXISTS last_refresh_status_code, "
        "DROP COLUMN IF EXISTS consecutive_refresh_failures, "
        "DROP COLUMN IF EXISTS refresh_failure_streak_started_at, "
        "DROP COLUMN IF EXISTS refresh_disabled_reason, "
        "DROP COLUMN IF EXISTS refresh_retry_after_at"
    )
