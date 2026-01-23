# Copyright 2025 thestill.me
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
PostgreSQL implementation of user repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Connection pooling via psycopg2 pool
- Thread-safe via connection pool
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

from ..models.user import User
from .user_repository import UserRepository

logger = logging.getLogger(__name__)


class PostgresUserRepository(UserRepository):
    """
    PostgreSQL-based user repository.

    Thread-safety: Uses connection pool for thread-safe access.
    """

    def __init__(self, database_url: str, min_connections: int = 1, max_connections: int = 10):
        """
        Initialize PostgreSQL user repository.

        Args:
            database_url: PostgreSQL connection URL
            min_connections: Minimum connections in pool
            max_connections: Maximum connections in pool
        """
        self.database_url = database_url
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            min_connections,
            max_connections,
            database_url,
        )
        logger.info("Initialized PostgreSQL user repository")

    def close(self):
        """Close all connections in the pool."""
        if self._pool:
            self._pool.closeall()

    @contextmanager
    def _get_connection(self):
        """Get database connection from pool."""
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by internal UUID (primary key)."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, email, name, picture, google_id, created_at, last_login_at
                    FROM users
                    WHERE id = %s
                """, (user_id,))

                row = cursor.fetchone()
                return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email address."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, email, name, picture, google_id, created_at, last_login_at
                    FROM users
                    WHERE email = %s
                """, (email,))

                row = cursor.fetchone()
                return self._row_to_user(row) if row else None

    def get_by_google_id(self, google_id: str) -> Optional[User]:
        """Get user by Google's unique user ID."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, email, name, picture, google_id, created_at, last_login_at
                    FROM users
                    WHERE google_id = %s
                """, (google_id,))

                row = cursor.fetchone()
                return self._row_to_user(row) if row else None

    def save(self, user: User) -> User:
        """
        Save or update a user.

        Uses UPSERT (INSERT ... ON CONFLICT) for atomic create-or-update.
        """
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO users (id, email, name, picture, google_id, created_at, last_login_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        name = EXCLUDED.name,
                        picture = EXCLUDED.picture,
                        google_id = COALESCE(EXCLUDED.google_id, users.google_id),
                        last_login_at = EXCLUDED.last_login_at
                """, (
                    user.id,
                    user.email,
                    user.name,
                    user.picture,
                    user.google_id,
                    user.created_at,
                    user.last_login_at,
                ))

            conn.commit()
            logger.debug(f"Saved user: {user.email}")
            return user

    def update_last_login(self, user_id: str) -> bool:
        """Update the last_login_at timestamp for a user."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE users
                    SET last_login_at = %s
                    WHERE id = %s
                """, (now, user_id))

                updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                logger.debug(f"Updated last login for user {user_id}")
            return updated

    def delete(self, user_id: str) -> bool:
        """Delete user by ID."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                deleted = cursor.rowcount > 0
            conn.commit()

            if deleted:
                logger.info(f"Deleted user: {user_id}")
            return deleted

    def _row_to_user(self, row: dict) -> User:
        """Convert database row to User model."""
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            picture=row["picture"],
            google_id=row["google_id"],
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
        )
