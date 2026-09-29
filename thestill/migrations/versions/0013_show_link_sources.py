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

"""Where each show link came from (spec #87 Phase 3c).

``podcasts.{apple,youtube,spotify}_url_source`` records whether a show link
was copied from the chart, stated by the publisher, found by a resolver or
set by hand. A ``curated`` value is never overwritten by the chart sync or
a resolver. Existing links are backfilled as ``chart`` when they equal the
chart row's value and ``publisher`` otherwise (the only other writer before
sources existed).

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS apple_url_source text NULL
    CHECK (apple_url_source IN ('chart', 'publisher', 'resolver', 'curated'));
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS youtube_url_source text NULL
    CHECK (youtube_url_source IN ('chart', 'publisher', 'resolver', 'curated'));
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS spotify_url_source text NULL
    CHECK (spotify_url_source IN ('chart', 'publisher', 'resolver', 'curated'));
"""

_BACKFILL = """
UPDATE podcasts AS p
   SET apple_url_source = CASE
           WHEN p.apple_url IS NULL THEN NULL
           WHEN p.apple_url = t.apple_url THEN 'chart'
           ELSE 'publisher' END,
       youtube_url_source = CASE
           WHEN p.youtube_url IS NULL THEN NULL
           WHEN p.youtube_url = t.youtube_url THEN 'chart'
           ELSE 'publisher' END
  FROM top_podcasts AS t
 WHERE t.rss_url = p.rss_url AND p.apple_url_source IS NULL AND p.youtube_url_source IS NULL;
UPDATE podcasts SET apple_url_source = 'publisher' WHERE apple_url IS NOT NULL AND apple_url_source IS NULL;
UPDATE podcasts SET youtube_url_source = 'publisher' WHERE youtube_url IS NOT NULL AND youtube_url_source IS NULL;
UPDATE podcasts SET spotify_url_source = 'publisher' WHERE spotify_url IS NOT NULL AND spotify_url_source IS NULL;
"""


def upgrade() -> None:
    op.execute(_DDL)
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS spotify_url_source")
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS youtube_url_source")
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS apple_url_source")
