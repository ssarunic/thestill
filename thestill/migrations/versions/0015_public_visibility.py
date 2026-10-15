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

"""Public-page visibility switches (spec #94).

``podcasts.public_pages`` is the publisher takedown switch: false hides the
show, its episodes and its quotes from every signed-out surface (public API,
page shell, sitemap). ``entities.public_hidden`` hides a person or entity
page and every quote they speak from the same surfaces. Both default to
today's behaviour, so the migration changes nothing until someone flips one.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS public_pages boolean NOT NULL DEFAULT true")
    op.execute("ALTER TABLE entities ADD COLUMN IF NOT EXISTS public_hidden boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS public_hidden")
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS public_pages")
