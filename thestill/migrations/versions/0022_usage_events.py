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

"""Usage ledger for per-user quotas (spec #104 Phase 2).

One row per costly action, written *before* the action runs as a
``reserved`` row and settled ``committed`` (money spent) or ``released``
(nothing spent). Reserved and committed rows count against a quota, so
concurrent requests can't all pass with one allowance left. A refusal is
recorded as a ``quota_exceeded`` row.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS usage_events (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL
        CHECK (kind IN ('feed_introduced', 'follow', 'import', 'translation', 'narration', 'quota_exceeded')),
    state text NOT NULL CHECK (state IN ('reserved', 'committed', 'released')),
    created_at timestamptz NOT NULL,
    settled_at timestamptz NULL,
    subject_id text NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_kind ON usage_events(user_id, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_kind_created ON usage_events(kind, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_reserved ON usage_events(created_at) WHERE state = 'reserved';
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS usage_events")
