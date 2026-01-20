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
Unit tests for authentication components.

Tests cover:
- User model and TokenPayload
- JWT utilities (create, decode, expiry)
- AuthService in single-user mode
- SqliteUserRepository
"""

import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.models.user import TokenPayload, User
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.auth_service import DEFAULT_USER_EMAIL, DEFAULT_USER_NAME, AuthService
from thestill.utils.config import Config
from thestill.utils.jwt import create_access_token, decode_token, get_token_expiry, is_token_expiring_soon


class TestUserModel:
    """Tests for User Pydantic model."""

    def test_user_creation_with_defaults(self):
        """User can be created with minimal fields."""
        user = User(email="test@example.com")

        assert user.email == "test@example.com"
        assert user.id is not None  # Auto-generated UUID
        assert user.name is None
        assert user.picture is None
        assert user.google_id is None
        assert user.created_at is not None
        assert user.last_login_at is None

    def test_user_creation_with_all_fields(self):
        """User can be created with all fields."""
        now = datetime.now(timezone.utc)
        user = User(
            id="test-id",
            email="test@example.com",
            name="Test User",
            picture="https://example.com/pic.jpg",
            google_id="google-123",
            created_at=now,
            last_login_at=now,
        )

        assert user.id == "test-id"
        assert user.email == "test@example.com"
        assert user.name == "Test User"
        assert user.picture == "https://example.com/pic.jpg"
        assert user.google_id == "google-123"

    def test_user_id_is_valid_uuid(self):
        """Auto-generated user ID is a valid UUID."""
        user = User(email="test@example.com")
        # Should not raise
        uuid.UUID(user.id)


class TestTokenPayload:
    """Tests for TokenPayload model."""

    def test_token_payload_fields(self):
        """TokenPayload has required fields."""
        now = datetime.now(timezone.utc)
        payload = TokenPayload(
            sub="user-123",
            exp=now + timedelta(days=30),
            iat=now,
        )

        assert payload.sub == "user-123"
        assert payload.exp > now
        assert payload.iat == now


class TestJwtUtilities:
    """Tests for JWT utility functions."""

    def test_create_access_token(self):
        """create_access_token returns a valid JWT string."""
        token = create_access_token(
            user_id="user-123",
            secret_key="test-secret",
            algorithm="HS256",
            expires_days=30,
        )

        assert isinstance(token, str)
        assert len(token) > 0
        # JWT has 3 parts separated by dots
        assert len(token.split(".")) == 3

    def test_decode_token_valid(self):
        """decode_token returns payload for valid token."""
        user_id = "user-123"
        secret = "test-secret"
        token = create_access_token(user_id, secret)

        payload = decode_token(token, secret)

        assert payload is not None
        assert payload.sub == user_id
        assert payload.exp > datetime.now(timezone.utc)

    def test_decode_token_invalid_secret(self):
        """decode_token returns None for wrong secret."""
        token = create_access_token("user-123", "correct-secret")

        payload = decode_token(token, "wrong-secret")

        assert payload is None

    def test_decode_token_expired(self):
        """decode_token returns None for expired token."""
        # Create token that expires immediately (negative days)
        token = create_access_token("user-123", "secret", expires_days=-1)

        payload = decode_token(token, "secret")

        assert payload is None

    def test_get_token_expiry(self):
        """get_token_expiry extracts expiry from token."""
        token = create_access_token("user-123", "secret", expires_days=30)

        expiry = get_token_expiry(token)

        assert expiry is not None
        assert expiry > datetime.now(timezone.utc)
        # Should be approximately 30 days from now
        expected = datetime.now(timezone.utc) + timedelta(days=30)
        assert abs((expiry - expected).total_seconds()) < 60  # Within 1 minute

    def test_get_token_expiry_invalid_token(self):
        """get_token_expiry returns None for invalid token."""
        expiry = get_token_expiry("not-a-valid-token")

        assert expiry is None

    def test_is_token_expiring_soon_false(self):
        """is_token_expiring_soon returns False for fresh token."""
        token = create_access_token("user-123", "secret", expires_days=30)

        result = is_token_expiring_soon(token, threshold_days=7)

        assert result is False

    def test_is_token_expiring_soon_true(self):
        """is_token_expiring_soon returns True for near-expiry token."""
        # Create token expiring in 3 days
        token = create_access_token("user-123", "secret", expires_days=3)

        result = is_token_expiring_soon(token, threshold_days=7)

        assert result is True


class TestSqliteUserRepository:
    """Tests for SqliteUserRepository."""

    @pytest.fixture
    def db_path(self):
        """Create a temporary database file."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name
        # Cleanup handled by tempfile

    @pytest.fixture
    def repo(self, db_path):
        """Create repository with initialized database."""
        # First create the users table
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY NOT NULL,
                email TEXT NOT NULL UNIQUE,
                name TEXT NULL,
                picture TEXT NULL,
                google_id TEXT UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP NULL
            )
        """
        )
        conn.commit()
        conn.close()

        return SqliteUserRepository(db_path)

    def test_save_and_get_by_id(self, repo):
        """Can save and retrieve user by ID."""
        user = User(
            id="test-id-1",
            email="test@example.com",
            name="Test User",
        )

        saved = repo.save(user)
        retrieved = repo.get_by_id("test-id-1")

        assert retrieved is not None
        assert retrieved.id == "test-id-1"
        assert retrieved.email == "test@example.com"
        assert retrieved.name == "Test User"

    def test_get_by_email(self, repo):
        """Can retrieve user by email."""
        user = User(email="unique@example.com", name="Unique User")
        repo.save(user)

        retrieved = repo.get_by_email("unique@example.com")

        assert retrieved is not None
        assert retrieved.email == "unique@example.com"

    def test_get_by_google_id(self, repo):
        """Can retrieve user by Google ID."""
        user = User(
            email="google@example.com",
            google_id="google-abc-123",
        )
        repo.save(user)

        retrieved = repo.get_by_google_id("google-abc-123")

        assert retrieved is not None
        assert retrieved.google_id == "google-abc-123"

    def test_get_nonexistent_returns_none(self, repo):
        """Getting nonexistent user returns None."""
        assert repo.get_by_id("nonexistent") is None
        assert repo.get_by_email("nonexistent@example.com") is None
        assert repo.get_by_google_id("nonexistent-google-id") is None

    def test_upsert_updates_existing(self, repo):
        """Saving user with same email updates existing."""
        user1 = User(id="id-1", email="same@example.com", name="Original")
        repo.save(user1)

        user2 = User(id="id-2", email="same@example.com", name="Updated")
        repo.save(user2)

        retrieved = repo.get_by_email("same@example.com")
        assert retrieved.name == "Updated"

    def test_delete(self, repo):
        """Can delete user."""
        user = User(id="to-delete", email="delete@example.com")
        repo.save(user)

        deleted = repo.delete("to-delete")
        assert deleted is True
        assert repo.get_by_id("to-delete") is None

    def test_delete_nonexistent(self, repo):
        """Deleting nonexistent user returns False."""
        deleted = repo.delete("nonexistent")
        assert deleted is False


class TestAuthServiceSingleUser:
    """Tests for AuthService in single-user mode."""

    @pytest.fixture
    def db_path(self):
        """Create a temporary database file."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name

    @pytest.fixture
    def user_repo(self, db_path):
        """Create user repository with initialized database."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY NOT NULL,
                email TEXT NOT NULL UNIQUE,
                name TEXT NULL,
                picture TEXT NULL,
                google_id TEXT UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP NULL
            )
        """
        )
        conn.commit()
        conn.close()

        return SqliteUserRepository(db_path)

    @pytest.fixture
    def config(self, db_path):
        """Create config for single-user mode."""
        return Config(
            storage_path=Path(db_path).parent,
            database_path=db_path,
            multi_user=False,
            jwt_secret_key="",  # Will be auto-generated
        )

    @pytest.fixture
    def auth_service(self, config, user_repo):
        """Create AuthService in single-user mode."""
        return AuthService(config, user_repo)

    def test_single_user_mode_detected(self, auth_service):
        """Service correctly identifies single-user mode."""
        assert auth_service.multi_user is False

    def test_get_or_create_default_user(self, auth_service):
        """Default user is created in single-user mode."""
        user = auth_service.get_or_create_default_user()

        assert user is not None
        assert user.email == DEFAULT_USER_EMAIL
        assert user.name == DEFAULT_USER_NAME

    def test_default_user_cached(self, auth_service):
        """Default user is cached after first retrieval."""
        user1 = auth_service.get_or_create_default_user()
        user2 = auth_service.get_or_create_default_user()

        assert user1.id == user2.id

    def test_get_current_user_returns_default(self, auth_service):
        """get_current_user returns default user without token."""
        user = auth_service.get_current_user(token=None)

        assert user is not None
        assert user.email == DEFAULT_USER_EMAIL

    def test_create_and_verify_jwt(self, auth_service):
        """Can create and verify JWT for user."""
        user = auth_service.get_or_create_default_user()

        token = auth_service.create_jwt(user)
        payload = auth_service.verify_jwt(token)

        assert payload is not None
        assert payload.sub == user.id

    def test_get_user_from_token(self, auth_service):
        """Can retrieve user from valid JWT."""
        user = auth_service.get_or_create_default_user()
        token = auth_service.create_jwt(user)

        retrieved = auth_service.get_user_from_token(token)

        assert retrieved is not None
        assert retrieved.id == user.id

    def test_google_auth_not_available_in_single_user(self, auth_service):
        """Google OAuth raises error in single-user mode."""
        with pytest.raises(RuntimeError, match="single-user mode"):
            auth_service.get_google_auth_url("http://localhost/callback")
