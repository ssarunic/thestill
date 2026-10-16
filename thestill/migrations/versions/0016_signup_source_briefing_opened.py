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

"""Signup source and briefing opened (spec #95 Phase 0).

``users.signup_source`` records which kind of public page an account was
created from; it is written once, on insert, and never updated.
``user_briefings.opened_at`` records the first time the owner opened a
briefing. Briefings marked listened before this column existed were
opened too, so ``opened_at`` is backfilled from ``listened_at``.

Same convergence contract as earlier migrations: the DDL (not the
backfill) also lives in ``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_source text NULL;
ALTER TABLE user_briefings ADD COLUMN IF NOT EXISTS opened_at timestamptz NULL;
"""

_BACKFILL = """
UPDATE user_briefings SET opened_at = listened_at
 WHERE opened_at IS NULL AND listened_at IS NOT NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.execute("ALTER TABLE user_briefings DROP COLUMN IF EXISTS opened_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS signup_source")
