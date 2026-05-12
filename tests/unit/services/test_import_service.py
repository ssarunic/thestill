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

"""Unit tests for ``ImportService`` (spec #31, Phase 1)."""

import sqlite3
import uuid

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.user import User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SYNTHETIC_AUDIO_IMPORTS_ID, SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.import_service import (
    BareAudioResolver,
    ImportService,
    ResolverError,
    UnsupportedUrlError,
    _normalise_url,
)

# ============================================================================
# BareAudioResolver
# ============================================================================


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/foo/bar.mp3", True),
        ("http://example.com/x.M4A", True),
        ("https://example.com/track.OPUS?q=1", True),
        ("https://example.com/foo.ogg", True),
        ("https://example.com/audio.wav", True),
        # Non-matches
        ("https://example.com/foo", False),
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://example.com/foo.txt", False),
        ("file:///local.mp3", False),  # only http(s)
    ],
)
def test_bare_audio_resolver_matches(url, expected):
    assert BareAudioResolver().matches(url) is expected


def test_bare_audio_resolver_canonical_id_is_stable_across_normalisation():
    resolver = BareAudioResolver()
    a = resolver.resolve("https://Example.com/foo/My_Episode.MP3?utm_source=x&fbclid=y")
    b = resolver.resolve("https://example.com/foo/My_Episode.MP3")
    c = resolver.resolve("https://example.com/foo/My_Episode.MP3?gclid=z")
    assert a.canonical_id == b.canonical_id == c.canonical_id
    assert a.canonical_id.startswith("audio:")
    assert len(a.canonical_id) == len("audio:") + 64  # sha256 hex


def test_bare_audio_resolver_path_case_is_preserved():
    """CDNs often treat path case as significant; we must not lowercase it."""
    resolver = BareAudioResolver()
    a = resolver.resolve("https://example.com/Foo/Bar.mp3")
    b = resolver.resolve("https://example.com/foo/bar.mp3")
    assert a.canonical_id != b.canonical_id


def test_bare_audio_resolver_title_derived_from_filename():
    src = BareAudioResolver().resolve("https://example.com/some/path/My_Cool-Talk.mp3")
    assert src.title == "My Cool Talk"
    assert src.source_handle == "example.com"
    assert src.kind == "bare_audio"


def test_normalise_url_drops_tracking_params_only():
    n = _normalise_url("https://Example.com/x.mp3?utm_source=a&utm_medium=b&keep=1&fbclid=c#frag")
    assert "utm_" not in n
    assert "fbclid" not in n
    assert "keep=1" in n
    assert "frag" not in n


# ============================================================================
# ImportService — fixtures
# ============================================================================


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "imports.db")
    SqlitePodcastRepository(path)  # runs migrations
    return path


@pytest.fixture
def repo(db_path):
    return SqlitePodcastRepository(db_path)


@pytest.fixture
def inbox_repo(db_path):
    return SqliteInboxRepository(db_path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def queue(db_path):
    return QueueManager(db_path)


@pytest.fixture
def service(repo, inbox_repo, queue):
    return ImportService(repository=repo, inbox_repository=inbox_repo, queue_manager=queue)


def _make_user(user_repo, email):
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


# ============================================================================
# ImportService — happy path + idempotency
# ============================================================================


def test_import_creates_synthetic_parent_episode_inbox_and_task(service, repo, inbox_repo, queue, user_repo, db_path):
    alice = _make_user(user_repo, "alice@example.com")

    result = service.import_url(user_id=alice.id, url="https://example.com/foo.mp3")

    assert result.episode_created is True
    assert result.inbox_created is True
    assert result.canonical_id.startswith("audio:")
    assert result.kind == "bare_audio"

    # Synthetic parent exists, marked synthetic=1, auto_added=0.
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, synthetic, auto_added FROM podcasts WHERE id = ?",
            (SYNTHETIC_AUDIO_IMPORTS_ID,),
        ).fetchone()
        assert dict(row) == {
            "id": SYNTHETIC_AUDIO_IMPORTS_ID,
            "synthetic": 1,
            "auto_added": 0,
        }
        # Episode points at the synthetic parent and carries canonical_id.
        ep = conn.execute(
            "SELECT podcast_id, canonical_id FROM episodes WHERE id = ?",
            (result.episode_id,),
        ).fetchone()
        assert ep["podcast_id"] == SYNTHETIC_AUDIO_IMPORTS_ID
        assert ep["canonical_id"] == result.canonical_id

    # Inbox row is the import source.
    entry = inbox_repo.get(alice.id, result.episode_id)
    assert entry is not None
    assert entry.source == "import"
    assert entry.state == "unread"

    # First pipeline task is TRANSCRIBE — imports skip download/downsample and
    # let Dalston fetch the audio from the URL directly.
    next_task = queue.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert next_task is not None
    assert next_task.episode_id == result.episode_id
    assert next_task.metadata.get("initiated_by") == "import"
    # No DOWNLOAD task should have been queued for an import.
    assert queue.get_next_task(stage=TaskStage.DOWNLOAD) is None


def test_reimport_same_url_same_user_is_idempotent(service, queue, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    url = "https://example.com/episodes/e1.mp3"

    r1 = service.import_url(user_id=alice.id, url=url)
    r2 = service.import_url(user_id=alice.id, url=url)

    assert r1.episode_id == r2.episode_id
    assert r2.episode_created is False
    assert r2.inbox_created is False

    # Only the first import should have queued a TRANSCRIBE task.
    first = queue.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert first is not None
    assert first.episode_id == r1.episode_id
    second = queue.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert second is None


def test_two_users_share_episode_each_gets_inbox_row(service, inbox_repo, queue, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    url = "https://cdn.example.com/track.mp3?utm_source=alice"

    r1 = service.import_url(user_id=alice.id, url=url)
    r2 = service.import_url(user_id=bob.id, url=url.replace("alice", "bob"))

    assert r1.episode_id == r2.episode_id  # canonical-id dedup ignores tracking params
    assert r2.episode_created is False
    assert r2.inbox_created is True

    assert inbox_repo.get(alice.id, r1.episode_id) is not None
    assert inbox_repo.get(bob.id, r1.episode_id) is not None

    # Pipeline should run exactly once.
    pending = []
    while True:
        t = queue.get_next_task(stage=TaskStage.TRANSCRIBE)
        if t is None:
            break
        pending.append(t)
    assert len(pending) == 1


# ============================================================================
# ImportService — error paths
# ============================================================================


def test_unsupported_url_raises(service, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    # Vimeo isn't covered by any v1 resolver.
    with pytest.raises(UnsupportedUrlError):
        service.import_url(user_id=alice.id, url="https://vimeo.com/123456789")


def test_resolver_failure_wraps_in_resolver_error(repo, inbox_repo, queue, user_repo):
    """A resolver that raises an arbitrary exception is surfaced as ResolverError."""
    alice = _make_user(user_repo, "alice@example.com")

    class BoomResolver:
        def matches(self, url):
            return True

        def resolve(self, url):
            raise RuntimeError("kaboom")

    svc = ImportService(
        repository=repo,
        inbox_repository=inbox_repo,
        queue_manager=queue,
        resolvers=[BoomResolver()],
    )
    with pytest.raises(ResolverError) as exc_info:
        svc.import_url(user_id=alice.id, url="https://example.com/anything")
    assert "kaboom" in str(exc_info.value)
