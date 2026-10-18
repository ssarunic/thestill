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

"""Episode views and playback time (spec #103 Phase 1).

``user_episode_views`` holds the first time each user opened each
episode; ``user_episode_progress.listened_at`` the last write that came
from playback, so "Mark as played / unplayed" never counts as listening.
Both exist only to measure, so the backfills skip users who turned usage
analytics off, like live writes (decision D5; GPC is per request and
can't be applied to history):

- views from inbox rows in ``read`` (their ``state_changed_at``);
- ``listened_at`` from ``updated_at`` where Phase 0 counted the row as a
  listen, so old windows read the same before and after.

Same convergence contract as earlier migrations: the DDL (not the
backfills) also lives in ``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS user_episode_views (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    first_viewed_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_episode_views_at ON user_episode_views(first_viewed_at);
ALTER TABLE user_episode_progress ADD COLUMN IF NOT EXISTS listened_at timestamptz NULL;
"""

_BACKFILL = """
INSERT INTO user_episode_views (user_id, episode_id, first_viewed_at)
SELECT i.user_id, i.episode_id, i.state_changed_at
  FROM user_episode_inbox i JOIN users u ON u.id = i.user_id
 WHERE i.state = 'read' AND i.state_changed_at IS NOT NULL AND u.usage_analytics_enabled
ON CONFLICT (user_id, episode_id) DO NOTHING;

UPDATE user_episode_progress p SET listened_at = p.updated_at
  FROM users u
 WHERE u.id = p.user_id AND u.usage_analytics_enabled
   AND p.listened_at IS NULL
   AND (p.position_seconds > 0 OR p.completed_at IS NOT NULL);
"""


def upgrade() -> None:
    op.execute(_DDL)
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.execute("ALTER TABLE user_episode_progress DROP COLUMN IF EXISTS listened_at")
    op.execute("DROP TABLE IF EXISTS user_episode_views CASCADE")
