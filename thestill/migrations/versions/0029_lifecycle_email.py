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

"""Lifecycle email (spec #118).

``users.product_tips_email_enabled`` is the Settings switch "Product tips by
email". Accounts that exist when this runs get ``false``: they signed up
before the sign-in page said tips would come, so they never had the chance
to refuse (PECR soft opt-in). The application turns it on for a new
account only when the sign-in page told them about tips (an email provider
is configured).

``user_messages`` is the send-once ledger: one row per (user, message,
channel). The UNIQUE constraint is the anchor that keeps several workers,
a restart or a retry from sending the same message twice. ``channel`` is
``email`` only for now; Slack and in-app messages share the ledger, so the
frequency rules and opt-outs apply across channels.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0029
Revises: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_DDL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS product_tips_email_enabled boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS user_messages (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_key text NOT NULL,
    channel text NOT NULL DEFAULT 'email' CHECK (channel IN ('email')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed','skipped')),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NULL,
    sent_at timestamptz NULL,
    last_error text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, message_key, channel)
);
CREATE INDEX IF NOT EXISTS idx_user_messages_due
    ON user_messages(next_attempt_at)
    WHERE status IN ('pending','sending');
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_messages CASCADE")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS product_tips_email_enabled")
