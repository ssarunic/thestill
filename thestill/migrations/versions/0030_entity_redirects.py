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

"""Entity redirects: where a deleted entity went.

A merge, a QID dedupe or an id-changing re-type deletes one entity row and
moves everything onto another. Without a record, the deleted entity's
public URL answers 404, so a URL Google had indexed (``/entities/person/
dr-sahar-yousef`` after the titled-guest merge) is lost instead of passed
on. ``delete_entity(..., redirect_to=...)`` writes one row here in the same
transaction, and the public page answers 301 to the keeper's URL.

No foreign keys: the source row no longer exists, and a target deleted
later simply stops redirecting (the page answers 404 as before).

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS entity_redirects (
    from_entity_id text PRIMARY KEY,
    to_entity_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entity_redirects_to ON entity_redirects(to_entity_id);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entity_redirects")
