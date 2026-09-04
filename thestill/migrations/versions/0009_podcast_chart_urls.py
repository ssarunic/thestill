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

"""Chart-sourced store links on the local podcast row (spec #73 follow-up).

The podcast detail page renders "Apple Podcasts" / "YouTube" links, but the
local ``podcasts`` row never carried those URLs — they only existed on the
``top_podcasts`` chart payload. Adds two nullable columns and backfills them
from the chart row with the same ``rss_url`` so podcasts imported before this
migration show the links too; later chart imports keep them in sync through
``PodcastRepository.sync_podcast_chart_urls``.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent), so ensure_schema-bootstrapped
databases already converge; this migration exists so Alembic-managed
production databases pick the change up through ``alembic upgrade`` alone.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS apple_url text NULL;
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS youtube_url text NULL;
"""

# Same statement shape as ``PodcastsMixin._backfill_chart_urls``: COALESCE
# keeps a stored link when the chart row has none.
_BACKFILL = """
UPDATE podcasts AS p
   SET apple_url = COALESCE(t.apple_url, p.apple_url),
       youtube_url = COALESCE(t.youtube_url, p.youtube_url)
  FROM top_podcasts AS t
 WHERE t.rss_url = p.rss_url
"""


def upgrade() -> None:
    op.execute(_DDL)
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS youtube_url")
    op.execute("ALTER TABLE podcasts DROP COLUMN IF EXISTS apple_url")
