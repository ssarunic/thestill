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

"""Account status, runtime settings, audit log, suspended follows (spec #104 Phase 1).

- ``users`` gains ``status`` (active / suspended / banned), the operator's
  ``status_reason`` with who and when, and ``tokens_valid_after`` — JWTs
  issued before it are rejected ("sign out everywhere").
- ``app_settings``: runtime switches that override env defaults.
- ``admin_audit_log``: append-only record of every admin action.
- ``suspended_follows``: a banned user's follows, moved aside so the
  follower gate stops processing feeds only they followed; an unban moves
  them back with their original ``created_at``.

Nothing to backfill: every existing user is ``active``.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'suspended', 'banned'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_reason text NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_changed_at timestamptz NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_changed_by uuid NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_valid_after timestamptz NULL;

CREATE TABLE IF NOT EXISTS app_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    actor_user_id uuid NULL,
    actor text NOT NULL CHECK (actor IN ('web', 'cli', 'system')),
    action text NOT NULL,
    target_user_id uuid NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit_log(target_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS suspended_follows (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    podcast_id uuid NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    suspended_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, podcast_id)
);
CREATE INDEX IF NOT EXISTS idx_suspended_follows_podcast ON suspended_follows(podcast_id);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS suspended_follows;
        DROP TABLE IF EXISTS admin_audit_log;
        DROP TABLE IF EXISTS app_settings;
        ALTER TABLE users DROP COLUMN IF EXISTS tokens_valid_after;
        ALTER TABLE users DROP COLUMN IF EXISTS status_changed_by;
        ALTER TABLE users DROP COLUMN IF EXISTS status_changed_at;
        ALTER TABLE users DROP COLUMN IF EXISTS status_reason;
        ALTER TABLE users DROP COLUMN IF EXISTS status;
        """
    )
