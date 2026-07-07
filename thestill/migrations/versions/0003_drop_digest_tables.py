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

"""Digest retirement: drop the legacy digest tables and the unused
briefings/briefing_episodes scaffold.

Per-user briefings (``user_briefings``, spec #36/#50) are the only
consumer-facing concept now. The ``briefings``/``briefing_episodes`` pair
was an anticipatory scaffold mirroring the digests schema that no
repository ever read or wrote. Historical digest markdown under
``data/digests/`` stays on disk; narration artefacts keyed by old digest
ids are orphaned by design (the narration join key is now briefing_id).

Downgrade intentionally recreates nothing — the digest feature's code is
gone, so resurrecting empty tables would only mask the mismatch.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TABLES = ["digest_episodes", "digests", "briefing_episodes", "briefings"]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade() -> None:
    # Irreversible by design; see module docstring.
    pass
