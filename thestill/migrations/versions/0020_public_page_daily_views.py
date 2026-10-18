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

"""Public page daily views (spec #103 Phase 2).

A per-day counter per public page, keyed by the object's id (``''`` for
the landing and top pages). No visitor id, IP, user agent or referrer.
Nothing to backfill: which page was viewed was never stored before.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS public_page_daily_views (
    day date NOT NULL,
    kind text NOT NULL,
    object_id text NOT NULL,
    views integer NOT NULL,
    PRIMARY KEY (day, kind, object_id)
);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public_page_daily_views")
