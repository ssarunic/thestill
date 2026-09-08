"""Per-user remote MCP tokens (spec #78 Phase 2).

``mcp_tokens`` also lives in ``postgres_schema.SCHEMA_SQL``, so databases
bootstrapped via ``ensure_schema`` already have it; the DDL here is
``IF NOT EXISTS`` and converges (same contract as 0002). This migration
exists so Alembic-managed production databases pick the table up through
``alembic upgrade`` alone.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tokens (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash text NOT NULL,
    token_prefix text NOT NULL,
    scopes text NOT NULL DEFAULT 'read',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NULL,
    last_used_at timestamptz NULL,
    last_used_ip text NULL,
    revoked_at timestamptz NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_tokens_hash ON mcp_tokens(token_hash);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "mcp_tokens" CASCADE')
