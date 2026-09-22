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

"""Live-linker decision cache (spec #81).

``entity_link_decisions`` remembers what the live Wikidata linker decided
for a spoken name, per podcast and then corpus-wide, so a recurring host or
company is looked up once. A NULL ``qid`` is a decided "none".

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent), so ensure_schema-bootstrapped
databases already converge; this migration exists so Alembic-managed
production databases pick the table up through ``alembic upgrade`` alone.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS entity_link_decisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surface_key text NOT NULL,
    podcast_id uuid NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    qid text NULL,
    label text NULL,
    description text NULL,
    confidence text NOT NULL CHECK (confidence IN ('high','medium','low')),
    reason text NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    linker_version text NOT NULL,
    hits integer NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_decisions_podcast
    ON entity_link_decisions(surface_key, podcast_id) WHERE podcast_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_decisions_corpus
    ON entity_link_decisions(surface_key) WHERE podcast_id IS NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "entity_link_decisions" CASCADE')
