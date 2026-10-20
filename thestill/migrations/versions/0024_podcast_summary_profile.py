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

"""Per-podcast summary profile override (spec #98).

``podcasts.summary_profile`` names the genre profile a show's summaries use
(``general``, later ``news``, ``sports``, ...). NULL — every existing row —
resolves the profile from the show's Apple categories, so the migration
changes nothing until an operator runs ``thestill set-summary-profile``.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS summary_profile text NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS summary_profile")
