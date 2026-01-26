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
SQLite implementation of user repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Follows the same patterns as SqlitePodcastRepository
- Thread-safe via connection-per-operation
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from structlog import get_logger

from ..models.user import User
from .user_repository import UserRepository

logger = get_logger(__name__)


class SqliteUserRepository(UserRepository):
    """
    SQLite-based user repository.

    Thread-safety: Uses context manager for per-operation connections.
    """

    def __init__(self, db_path: str):
        """
        Initialize SQLite user repository.

        Args:
            db_path: Path to SQLite database file (e.g., "./data/podcasts.db")
        """
        self.db_path = Path(db_path)
        logger.info(f"Initialized SQLite user repository: {self.db_path}")

    @contextmanager
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get database connection with proper setup.

        Features:
        - Row factory for dict-like access
        - Foreign keys enabled
        - Automatic commit/rollback
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by internal UUID (primary key)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, email, name, picture, google_id, created_at, last_login_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            )

            row = cursor.fetchone()
            return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email address."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, email, name, picture, google_id, created_at, last_login_at
                FROM users
                WHERE email = ?
                """,
                (email,),
            )

            row = cursor.fetchone()
            return self._row_to_user(row) if row else None

    def get_by_google_id(self, google_id: str) -> Optional[User]:
        """Get user by Google's unique user ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, email, name, picture, google_id, created_at, last_login_at
                FROM users
                WHERE google_id = ?
                """,
                (google_id,),
            )

            row = cursor.fetchone()
            return self._row_to_user(row) if row else None

    def save(self, user: User) -> User:
        """
        Save or update a user.

        Uses UPSERT (INSERT ... ON CONFLICT) for atomic create-or-update.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, name, picture, google_id, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    name = excluded.name,
                    picture = excluded.picture,
                    google_id = COALESCE(excluded.google_id, users.google_id),
                    last_login_at = excluded.last_login_at
                """,
                (
                    user.id,
                    user.email,
                    user.name,
                    user.picture,
                    user.google_id,
                    user.created_at.isoformat(),
                    user.last_login_at.isoformat() if user.last_login_at else None,
                ),
            )

            logger.debug(f"Saved user: {user.email}")
            return user

    def update_last_login(self, user_id: str) -> bool:
        """Update the last_login_at timestamp for a user."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE users
                SET last_login_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), user_id),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.debug(f"Updated last login for user {user_id}")
            return updated

    def delete(self, user_id: str) -> bool:
        """Delete user by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM users WHERE id = ?
                """,
                (user_id,),
            )

            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted user: {user_id}")
            return deleted

    def _row_to_user(self, row: sqlite3.Row) -> User:
        """Convert database row to User model."""
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            picture=row["picture"],
            google_id=row["google_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None,
        )
