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

"""
PostgreSQL implementation of the user repository (spec #44 — vertical slice).

Ported from ``SqliteUserRepository`` behind the shared ``UserRepository`` ABC,
applying the spec #44 dialect checklist:

- ``?`` placeholders → ``%s`` (psycopg).
- SQLite ``0/1`` integer booleans → native ``boolean`` (``region_locked``,
  ``is_admin``) — pass/return Python ``bool`` directly, no ``1 if x else 0``.
- Text-ISO timestamps → ``timestamptz``; pass tz-aware ``datetime`` objects
  and read them back as tz-aware, which *removes* the SQLite text-timestamp
  foot-gun rather than porting it (aligns with spec #42 FM-3).
- ``INSERT ... ON CONFLICT`` upserts carry over unchanged (Postgres uses the
  same clause; ``excluded`` → ``EXCLUDED``).

Business logic is identical to the SQLite version — only the dialect differs,
which is the whole point of the repository seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from structlog import get_logger

from ..models.user import User
from ..utils.postgres_ext import connect
from .user_repository import UserRepository

logger = get_logger(__name__)

_SELECT_COLS = "id, email, name, picture, google_id, created_at, last_login_at, region, region_locked, is_admin"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id text PRIMARY KEY NOT NULL,
    email text NOT NULL UNIQUE,
    name text NULL,
    picture text NULL,
    google_id text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz NULL,
    region text NULL,
    region_locked boolean NOT NULL DEFAULT false,
    is_admin boolean NOT NULL DEFAULT false,
    CHECK (length(id) = 36),
    CHECK (length(email) > 0),
    CHECK (region IS NULL OR length(region) = 2)
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti text PRIMARY KEY NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens(expires_at);
"""


class PostgresUserRepository(UserRepository):
    """PostgreSQL-backed user repository. Thread-safe via connection-per-op."""

    def __init__(self, dsn: str, *, ensure_schema: bool = True):
        """
        Args:
            dsn: psycopg connection string, e.g.
                ``postgresql://user:pass@host:5432/thestill``.
            ensure_schema: create the ``users`` / ``revoked_tokens`` tables if
                absent (default). The real migration will move DDL into
                alembic (spec #44 Phase 5); until then each repo ensures its
                own tables, mirroring the SQLite repos' ``_ensure_table``.
        """
        self.dsn = dsn
        if ensure_schema:
            self._ensure_schema()
        logger.info("Initialized Postgres user repository")

    def _ensure_schema(self) -> None:
        with connect(self.dsn) as conn:
            conn.execute(_SCHEMA)

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by internal UUID (primary key)."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email address."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM users WHERE email = %s",
                (email,),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def get_by_google_id(self, google_id: str) -> Optional[User]:
        """Get user by Google's unique user ID."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM users WHERE google_id = %s",
                (google_id,),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def save(self, user: User) -> User:
        """
        Save or update a user via ``INSERT ... ON CONFLICT`` upsert.

        As with the SQLite path, ``region`` / ``region_locked`` / ``is_admin``
        are intentionally NOT updated on conflict — preserving stored region
        and admin grant across logins. Use ``update_region`` to mutate region.
        """
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO users (
                    id, email, name, picture, google_id,
                    created_at, last_login_at, region, region_locked, is_admin
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    name = EXCLUDED.name,
                    picture = EXCLUDED.picture,
                    google_id = COALESCE(EXCLUDED.google_id, users.google_id),
                    last_login_at = EXCLUDED.last_login_at
                """,
                (
                    user.id,
                    user.email,
                    user.name,
                    user.picture,
                    user.google_id,
                    user.created_at,
                    user.last_login_at,
                    user.region,
                    user.region_locked,
                    user.is_admin,
                ),
            )
            logger.debug("Saved user", email=user.email)
            return user

    def update_region(self, user_id: str, region: Optional[str], locked: bool) -> bool:
        """Update region + lock flag. Region is normalised to lowercase."""
        normalised = region.lower() if region else None
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "UPDATE users SET region = %s, region_locked = %s WHERE id = %s",
                (normalised, locked, user_id),
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.debug("user_region_updated", user_id=user_id, region=normalised, locked=locked)
            return updated

    def update_last_login(self, user_id: str) -> bool:
        """Update the last_login_at timestamp for a user."""
        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "UPDATE users SET last_login_at = %s WHERE id = %s",
                (now, user_id),
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.debug("Updated last login", user_id=user_id)
            return updated

    def delete(self, user_id: str) -> bool:
        """Delete user by ID."""
        with connect(self.dsn) as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Deleted user", user_id=user_id)
            return deleted

    # ----- JWT revocation deny-list (spec #25 item 4.2) -----

    def revoke_token(self, jti: str, expires_at: datetime) -> None:
        """Persist a jti to the revocation deny-list (idempotent)."""
        if not jti:
            return  # Legacy tokens with no jti can't be revoked precisely.
        with connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO revoked_tokens (jti, expires_at) VALUES (%s, %s) ON CONFLICT (jti) DO NOTHING",
                (jti, expires_at),
            )
            logger.info("token_revoked", jti=jti)

    def is_token_revoked(self, jti: str) -> bool:
        """Return True iff ``jti`` is currently on the deny-list."""
        if not jti:
            return False
        with connect(self.dsn) as conn:
            row = conn.execute("SELECT 1 FROM revoked_tokens WHERE jti = %s", (jti,)).fetchone()
            return row is not None

    def prune_expired_revocations(self) -> int:
        """Drop revoked rows whose ``expires_at`` is past."""
        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            cursor = conn.execute("DELETE FROM revoked_tokens WHERE expires_at < %s", (now,))
            count = cursor.rowcount
            if count:
                logger.debug("pruned_expired_revocations", count=count)
            return count

    def _row_to_user(self, row: dict) -> User:
        """Convert a dict row to a User. timestamptz columns come back as
        tz-aware ``datetime`` — no string parsing needed (unlike SQLite)."""
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            picture=row["picture"],
            google_id=row["google_id"],
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
            region=row["region"],
            region_locked=bool(row["region_locked"]),
            is_admin=bool(row["is_admin"]),
        )
