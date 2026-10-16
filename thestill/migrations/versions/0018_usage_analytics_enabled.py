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

"""Per-user usage-events switch (spec #95 Layer B).

``users.usage_analytics_enabled`` backs the Settings toggle for product
usage events. Off means the browser loads no analytics code and the server
drops every event carrying that user. Defaults to on.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS usage_analytics_enabled boolean NOT NULL DEFAULT true")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS usage_analytics_enabled")
