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

"""Transcript editor op log (spec #110 §2).

``transcript_edits`` is append-only: one row per admin correction, keyed
to the durable segment anchors of spec #18. ``transcript_edit_refreshes``
remembers the last op each episode's downstream refresh (spec #106
``--from entities``) was queued for, so the periodic sweep can catch a
session that never pressed Finish.

The same DDL lives in ``postgres_schema.SCHEMA_SQL``; every statement is
``IF NOT EXISTS`` so either path converges (same contract as 0009).

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS transcript_edits (
    id uuid PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    seq integer NOT NULL,
    op text NOT NULL,
    raw_sha256 text NOT NULL,
    anchor_json jsonb NOT NULL,
    args_json jsonb NOT NULL,
    before_json jsonb NOT NULL,
    reverts uuid NULL REFERENCES transcript_edits(id),
    session_id text NOT NULL,
    report_id text NULL,
    actor_user_id uuid NULL,
    actor_kind text NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (episode_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_transcript_edits_actor ON transcript_edits(actor_user_id, created_at);

CREATE TABLE IF NOT EXISTS transcript_edit_refreshes (
    episode_id uuid PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
    refreshed_seq integer NOT NULL,
    queued_at timestamptz NOT NULL
);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "transcript_edit_refreshes" CASCADE')
    op.execute('DROP TABLE IF EXISTS "transcript_edits" CASCADE')
