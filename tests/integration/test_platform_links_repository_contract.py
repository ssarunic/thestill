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

"""Dual-backend contract suite for per-episode platform links (spec #87).

Runs against SQLite and, when ``TEST_DATABASE_URL`` points at a reachable
scratch Postgres, against the ``EpisodesMixin`` port too — same fidelity
contract as ``test_podcast_repository_episodes_contract.py``:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_scratch \\
        ./venv/bin/python -m pytest tests/integration/test_platform_links_repository_contract.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from thestill.models.podcast import Episode, PlatformLink, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)


@pytest.fixture(params=["sqlite", "postgres"])
def h(request, tmp_path):
    if request.param == "sqlite":
        repo = SqlitePodcastRepository(db_path=str(tmp_path / "links.db"))

        def make_podcast(podcast: Podcast) -> str:
            repo.save(podcast)
            return podcast.id

        def delete_episode(episode_id: str) -> None:
            # The repo connection loads sqlite-vec, which the episode cascade
            # (chunk embeddings) needs; a bare sqlite3.connect cannot.
            with repo._get_connection() as conn:
                conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))

        yield SimpleNamespace(repo=repo, make_podcast=make_podcast, delete_episode=delete_episode, backend="sqlite")
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")

    import psycopg

    from thestill.repositories.postgres_podcast_repository_episodes import EpisodesMixin
    from thestill.repositories.postgres_podcast_repository_podcasts import PodcastsMixin
    from thestill.repositories.postgres_schema import ensure_schema

    class _PgRepo(PodcastsMixin, EpisodesMixin):
        def __init__(self, dsn):
            self.dsn = dsn

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE episodes, episode_platform_links, episode_alternate_enclosures, podcasts CASCADE")

    def make_podcast(podcast: Podcast) -> str:
        with psycopg.connect(PG_DSN) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug, description, apple_url) VALUES (%s, %s, %s, %s, 'd', %s)",
                (
                    podcast.id,
                    str(podcast.rss_url),
                    podcast.title,
                    podcast.slug or f"pod-{podcast.id[:8]}",
                    podcast.apple_url,
                ),
            )
            for ep in podcast.episodes:
                conn.execute(
                    "INSERT INTO episodes (id, podcast_id, external_id, title, description, audio_url, pub_date, "
                    "created_at, duration, description_html, website_url) "
                    "VALUES (%s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s)",
                    (
                        ep.id,
                        podcast.id,
                        ep.external_id,
                        ep.title,
                        str(ep.audio_url),
                        ep.pub_date,
                        ep.created_at,
                        ep.duration,
                        ep.description_html,
                        ep.website_url,
                    ),
                )
        return podcast.id

    def delete_episode(episode_id: str) -> None:
        with psycopg.connect(PG_DSN) as conn:
            conn.execute("DELETE FROM episodes WHERE id = %s", (episode_id,))

    yield SimpleNamespace(
        repo=_PgRepo(PG_DSN), make_podcast=make_podcast, delete_episode=delete_episode, backend="postgres"
    )


def _episode(n: int, pub_date=None) -> Episode:
    return Episode(
        id=str(uuid.uuid4()),
        external_id=f"guid-{n}-{uuid.uuid4().hex[:6]}",
        title=f"Episode {n}",
        description="",
        audio_url=f"https://cdn.example.com/{n}.mp3",
        pub_date=pub_date,
        created_at=NOW - timedelta(days=n),
        duration=1800,
        description_html='<a href="https://open.spotify.com/episode/0tkEdaVIsQNGKUGUKTPqeH">x</a>',
        website_url=f"https://example.com/ep/{n}",
    )


def _podcast(episodes, apple_url=None) -> Podcast:
    uid = uuid.uuid4().hex[:8]
    return Podcast(
        id=str(uuid.uuid4()),
        rss_url=f"https://example.com/{uid}/feed.xml",
        title=f"Podcast {uid}",
        slug=f"pod-{uid}",
        description="d",
        apple_url=apple_url,
        episodes=episodes,
    )


def _link(episode_id, url=None, method=None, checked_at=NOW, ref=None) -> PlatformLink:
    return PlatformLink(
        episode_id=episode_id, platform="apple", url=url, external_ref=ref, match_method=method, checked_at=checked_at
    )


class TestCandidates:
    def test_window_is_newest_by_pub_date_taken_before_link_filter(self, h):
        eps = [_episode(n, pub_date=NOW - timedelta(days=n)) for n in range(5)]
        pid = h.make_podcast(_podcast(eps))

        got = h.repo.get_platform_link_candidates(pid, "apple", window=3, recheck_before=None)
        assert [c.episode_id for c in got] == [eps[0].id, eps[1].id, eps[2].id]
        assert got[0].external_id == eps[0].external_id
        assert got[0].audio_url == "https://cdn.example.com/0.mp3"
        assert got[0].title == "Episode 0"
        assert got[0].pub_date == NOW
        assert got[0].duration == 1800
        assert got[0].description_html == '<a href="https://open.spotify.com/episode/0tkEdaVIsQNGKUGUKTPqeH">x</a>'
        assert got[0].website_url == "https://example.com/ep/0"
        assert got[0].canonical_id is None

        # Linking the newest keeps the window anchored: the 4th-newest does NOT enter.
        h.repo.upsert_platform_links([_link(eps[0].id, url="https://podcasts.apple.com/x?i=1", method="guid")])
        got = h.repo.get_platform_link_candidates(pid, "apple", window=3, recheck_before=None)
        assert [c.episode_id for c in got] == [eps[1].id, eps[2].id]

    def test_undated_episodes_sort_last(self, h):
        dated = _episode(1, pub_date=NOW - timedelta(days=1))
        undated = _episode(2, pub_date=None)
        pid = h.make_podcast(_podcast([undated, dated]))
        got = h.repo.get_platform_link_candidates(pid, "apple", window=10, recheck_before=None)
        assert [c.episode_id for c in got] == [dated.id, undated.id]
        assert got[1].pub_date is None

    def test_not_found_marker_suppresses_until_recheck(self, h):
        ep = _episode(1, pub_date=NOW)
        pid = h.make_podcast(_podcast([ep]))
        h.repo.upsert_platform_links([_link(ep.id, checked_at=NOW - timedelta(hours=2))])

        fresh = h.repo.get_platform_link_candidates(pid, "apple", window=10, recheck_before=NOW - timedelta(hours=24))
        stale = h.repo.get_platform_link_candidates(pid, "apple", window=10, recheck_before=NOW - timedelta(hours=1))
        forced = h.repo.get_platform_link_candidates(pid, "apple", window=10, recheck_before=None)
        assert fresh == []
        assert [c.episode_id for c in stale] == [ep.id]
        assert [c.episode_id for c in forced] == [ep.id]

    def test_other_platform_rows_do_not_count(self, h):
        ep = _episode(1, pub_date=NOW)
        pid = h.make_podcast(_podcast([ep]))
        h.repo.upsert_platform_links(
            [PlatformLink(episode_id=ep.id, platform="youtube", url="https://youtube.com/watch?v=x", checked_at=NOW)]
        )
        got = h.repo.get_platform_link_candidates(pid, "apple", window=10, recheck_before=NOW)
        assert [c.episode_id for c in got] == [ep.id]

    def test_scoped_to_the_podcast(self, h):
        mine = _episode(1, pub_date=NOW)
        theirs = _episode(2, pub_date=NOW)
        pid = h.make_podcast(_podcast([mine]))
        h.make_podcast(_podcast([theirs]))
        got = h.repo.get_platform_link_candidates(pid, "apple", window=10, recheck_before=None)
        assert [c.episode_id for c in got] == [mine.id]


class TestUpsertAndRead:
    def test_found_rows_are_read_back_in_platform_order(self, h):
        ep = _episode(1, pub_date=NOW)
        h.make_podcast(_podcast([ep]))
        rows = [
            PlatformLink(episode_id=ep.id, platform="youtube", url="https://youtube.com/watch?v=x", checked_at=NOW),
            _link(ep.id, url="https://podcasts.apple.com/x?i=1", method="guid", ref="1"),
        ]
        assert h.repo.upsert_platform_links(rows) == 2

        got = h.repo.get_platform_links(ep.id)
        assert [(l.platform, l.url, l.match_method, l.external_ref) for l in got] == [
            ("apple", "https://podcasts.apple.com/x?i=1", "guid", "1"),
            ("youtube", "https://youtube.com/watch?v=x", None, None),
        ]
        assert got[0].checked_at == NOW
        assert got[0].created_at is not None and got[0].id is not None

    def test_not_found_marker_is_invisible_to_readers(self, h):
        ep = _episode(1, pub_date=NOW)
        h.make_podcast(_podcast([ep]))
        h.repo.upsert_platform_links([_link(ep.id)])
        assert h.repo.get_platform_links(ep.id) == []

    def test_not_found_pass_never_blanks_a_stored_url(self, h):
        ep = _episode(1, pub_date=NOW)
        h.make_podcast(_podcast([ep]))
        h.repo.upsert_platform_links([_link(ep.id, url="https://podcasts.apple.com/x?i=1", method="guid", ref="1")])
        h.repo.upsert_platform_links([_link(ep.id, checked_at=NOW + timedelta(hours=1))])

        (row,) = h.repo.get_platform_links(ep.id)
        assert (row.url, row.match_method, row.external_ref) == ("https://podcasts.apple.com/x?i=1", "guid", "1")
        assert row.checked_at == NOW + timedelta(hours=1)

    def test_relink_replaces_url_and_method(self, h):
        ep = _episode(1, pub_date=NOW)
        h.make_podcast(_podcast([ep]))
        h.repo.upsert_platform_links(
            [_link(ep.id, url="https://podcasts.apple.com/x?i=1", method="title_date", ref="1")]
        )
        h.repo.upsert_platform_links([_link(ep.id, url="https://podcasts.apple.com/x?i=2", method="guid", ref="2")])
        (row,) = h.repo.get_platform_links(ep.id)
        assert (row.url, row.match_method, row.external_ref) == ("https://podcasts.apple.com/x?i=2", "guid", "2")

    def test_empty_upsert_is_a_noop(self, h):
        assert h.repo.upsert_platform_links([]) == 0

    def test_rows_cascade_with_the_episode(self, h):
        ep = _episode(1, pub_date=NOW)
        h.make_podcast(_podcast([ep]))
        h.repo.upsert_platform_links([_link(ep.id, url="https://podcasts.apple.com/x?i=1", method="guid")])
        h.delete_episode(ep.id)
        assert h.repo.get_platform_links(ep.id) == []


class TestShowUrl:
    def test_set_podcast_apple_url_is_read_back_by_chart_sync(self, h):
        pid = h.make_podcast(_podcast([]))
        assert h.repo.sync_podcast_chart_urls(pid)["apple_url"] is None
        h.repo.set_podcast_platform_url(pid, "apple", "https://podcasts.apple.com/us/podcast/x/id123")
        h.repo.set_podcast_platform_url(pid, "spotify", "https://open.spotify.com/show/2MAi0BvDc6GTFvKFPXnkCL")
        urls = h.repo.sync_podcast_chart_urls(pid)
        assert urls["apple_url"] == "https://podcasts.apple.com/us/podcast/x/id123"
        assert urls["spotify_url"] == "https://open.spotify.com/show/2MAi0BvDc6GTFvKFPXnkCL"
        assert urls["youtube_url"] is None
        with pytest.raises(KeyError):
            h.repo.set_podcast_platform_url(pid, "mixcloud", "https://example.com")
