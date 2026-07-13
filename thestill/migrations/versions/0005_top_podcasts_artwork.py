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

"""Top-podcasts chart artwork.

Adds ``top_podcasts.image_url`` so the region chart can render a cover for
every entry — sourced from Apple's ``artworkUrl600`` at scrape time — instead
of only for podcasts that have already been imported into ``podcasts`` locally.

Same convergence contract as the earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent), so ensure_schema-bootstrapped
databases already converge; this migration exists so Alembic-managed
production databases pick the change up through ``alembic upgrade`` alone.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE top_podcasts
    ADD COLUMN IF NOT EXISTS image_url text NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("ALTER TABLE top_podcasts DROP COLUMN IF EXISTS image_url")
