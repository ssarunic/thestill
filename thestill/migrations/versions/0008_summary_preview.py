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

"""Stored summary preview (spec #69 Phase 6.5).

The episode list endpoint used to read each listed episode's full summary
file (an S3 round-trip per row on the hosted backend) just to compute a
~200-char preview at request time. The preview is now computed once at
summarize time and stored on the episode row; rows summarized before this
column existed backfill lazily on first list render.

No data backfill here — the preview lives in summary *files*, not in SQL.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL``.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE episodes ADD COLUMN IF NOT EXISTS summary_preview text NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE episodes DROP COLUMN IF EXISTS summary_preview")
