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

"""OAuth authorization server for the remote MCP server (spec #109).

The same DDL lives in ``postgres_schema.SCHEMA_SQL``; every statement is
``IF NOT EXISTS`` so either path converges (same contract as 0009).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-27
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id text PRIMARY KEY,
    source text NOT NULL CHECK (source IN ('cimd', 'dcr')),
    client_name text NULL,
    metadata jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    fetched_at timestamptz NULL,
    fresh_until timestamptz NULL
);

CREATE TABLE IF NOT EXISTS oauth_pending (
    id_hash text PRIMARY KEY,
    client_id text NOT NULL,
    redirect_uri text NOT NULL,
    redirect_uri_explicit boolean NOT NULL,
    state text NULL,
    scopes text NOT NULL,
    code_challenge text NOT NULL,
    resource text NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_pending_expires ON oauth_pending(expires_at);

CREATE TABLE IF NOT EXISTS oauth_grants (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id text NOT NULL,
    client_name text NULL,
    redirect_host text NOT NULL,
    scopes text NOT NULL,
    created_at timestamptz NOT NULL,
    last_used_at timestamptz NULL,
    last_used_ip text NULL,
    revoked_at timestamptz NULL,
    revoked_reason text NULL CHECK (revoked_reason IN ('user', 'admin', 'replaced', 'reuse'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_grants_active
    ON oauth_grants(user_id, client_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oauth_grants_client ON oauth_grants(client_id);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash text PRIMARY KEY,
    grant_id uuid NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
    client_id text NOT NULL,
    scopes text NOT NULL,
    redirect_uri text NOT NULL,
    redirect_uri_explicit boolean NOT NULL,
    code_challenge text NOT NULL,
    resource text NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_grant ON oauth_codes(grant_id);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    token_hash text PRIMARY KEY,
    grant_id uuid NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
    client_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('access', 'refresh')),
    scopes text NOT NULL,
    resource text NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NULL,
    replaced_by_hash text NULL,
    revoked_at timestamptz NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_grant ON oauth_tokens(grant_id);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_expires ON oauth_tokens(expires_at);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    for table in ("oauth_tokens", "oauth_codes", "oauth_grants", "oauth_pending", "oauth_clients"):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
