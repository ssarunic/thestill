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

"""Quote shares: the short links of spec #114 and their opens.

``quote_shares`` holds one row per shareable quote. The id is a truncated
hash of the episode, the start time and the quoted words, so it cannot be
reversed: ``/q/<id>`` is a lookup of this table, never a search of the
quotes. ``text`` is a snapshot of what was shared; ``start_ms``-``end_ms``
is the source turn's time range when the row was minted, the anchor the
speaker is re-resolved against after a transcript edit. ``speaker_entity_id``
has no foreign key: an entity merge deletes the losing row, and the share
follows the merge instead of losing its speaker.

``quote_share_hits`` counts opens of a short link by browsers, per day and
referrer host. No visitor id, IP or user agent.

Same convergence contract as earlier migrations: the DDL also lives in
``postgres_schema.SCHEMA_SQL`` (idempotent).

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS quote_shares (
    id text PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    podcast_id uuid NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    segment_id bigint NOT NULL,
    start_ms bigint NOT NULL,
    end_ms bigint NOT NULL,
    text text NOT NULL,
    speaker_entity_id text NULL,
    source text NOT NULL DEFAULT 'gated',
    created_by uuid NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz NULL,
    revoked_reason text NULL,
    template_version smallint NOT NULL DEFAULT 1,
    audio_key text NULL
);
CREATE INDEX IF NOT EXISTS idx_quote_shares_episode ON quote_shares(episode_id);
CREATE INDEX IF NOT EXISTS idx_quote_shares_podcast ON quote_shares(podcast_id);
CREATE TABLE IF NOT EXISTS quote_share_hits (
    share_id text NOT NULL REFERENCES quote_shares(id) ON DELETE CASCADE,
    day date NOT NULL,
    referrer_host text NOT NULL DEFAULT '',
    hits integer NOT NULL DEFAULT 0,
    PRIMARY KEY (share_id, day, referrer_host)
);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quote_share_hits")
    op.execute("DROP TABLE IF EXISTS quote_shares")
