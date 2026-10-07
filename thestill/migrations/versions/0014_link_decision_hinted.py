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

"""Whether a live-linker decision saw a Resource List hint (spec #92 Stage 5).

``entity_link_decisions.hinted`` is true when the chooser was shown the
summary's kind and description for the name. A cached "none" decided
without a hint is decided once more when a hint exists; the column is what
stops it being decided again. NULL (every row before this migration) reads
as "not hinted".

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE entity_link_decisions ADD COLUMN IF NOT EXISTS hinted boolean NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE entity_link_decisions DROP COLUMN IF EXISTS hinted")
