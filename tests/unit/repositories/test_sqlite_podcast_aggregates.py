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

"""Spec #69 Phase 4 — SQL aggregate/light-lookup overrides.

Parity tests: each SQLite SQL override must return exactly what the
``PodcastRepository`` fallback (which derives the same answer from full
``get_all()`` hydration) computes over the same data. The fallback is the
executable contract; the override is the fast path.
"""

from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.podcast import Episode, Podcast
from thestill.repositories.podcast_repository import EpisodeRepository, PodcastRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo(tmp_path):
    return SqlitePodcastRepository(str(tmp_path / "test.db"))


def _episode(n: int, **paths) -> Episode:
    return Episode(
        title=f"Episode {n}",
        description="d",
        audio_url=f"https://example.com/ep{n}.mp3",
        external_id=f"ep-{n}",
        slug=f"episode-{n}",
        pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=n),
        **paths,
    )


@pytest.fixture
def seeded(repo):
    """Three podcasts covering every pipeline state plus a failed episode."""
    base = dict(description="d", language="en")
    p1 = Podcast(
        title="Alpha Show",
        rss_url="https://example.com/alpha.rss",
        slug="alpha-show",
        author="NYT",
        episodes=[
            _episode(1),  # discovered
            _episode(2, audio_path="a.mp3"),  # downloaded
            _episode(3, audio_path="a.mp3", downsampled_audio_path="a.wav"),  # downsampled
            _episode(4, audio_path="a.mp3", downsampled_audio_path="a.wav", raw_transcript_path="r.json"),
        ],
        **base,
    )
    p2 = Podcast(
        title="Beta Cast",
        rss_url="https://example.com/beta.rss",
        slug="beta-cast",
        author="Spotify",
        episodes=[
            _episode(5, clean_transcript_path="c.md"),  # cleaned
            _episode(6, clean_transcript_path="c.md", summary_path="s.md"),  # summarized
            _episode(7, summary_path="s.md", failed_at_stage="summarize", failed_at=datetime.now(timezone.utc)),
        ],
        **base,
    )
    p3 = Podcast(
        title="Gamma Hour",
        rss_url="https://example.com/gamma.rss",
        slug="gamma-hour",
        episodes=[],
        **base,
    )
    for p in (p1, p2, p3):
        repo.save(p)
    return repo


def test_count_podcasts_matches_fallback(seeded):
    assert seeded.count_podcasts() == PodcastRepository.count_podcasts(seeded) == 3


def test_resolve_podcast_slug_matches_fallback(seeded):
    for slug in ("alpha-show", "beta-cast", "missing"):
        assert seeded.resolve_podcast_slug(slug) == PodcastRepository.resolve_podcast_slug(seeded, slug)
    assert seeded.resolve_podcast_slug("missing") is None


def test_count_episode_states_matches_fallback(seeded):
    sql = seeded.count_episode_states()
    fallback = PodcastRepository.count_episode_states(seeded)
    assert sql == fallback
    # Spot-check the shape against the seeded fixture.
    assert sql["episodes_total"] == 7
    assert sql["failed"] == 1
    assert sql["summarized"] == 1  # the failed one counts only under failed
    assert sql["with_summary_path"] == 2  # ...but does count here


def _normalize(rows):
    out = []
    for row in rows:
        row = dict(row)
        for key in ("updated_at", "pub_date", "last_processed", "last_processed_at"):
            if key in row and isinstance(row[key], datetime):
                row[key] = row[key].isoformat()
        out.append(row)
    return out


def test_recent_activity_rows_match_fallback(seeded):
    # All fixture episodes share near-identical updated_at (one batch save),
    # so tie order is unspecified — fetch everything and compare
    # order-insensitively, then assert the ordering property separately.
    sql_rows, sql_total = seeded.get_recent_activity_rows(limit=10)
    fb_rows, fb_total = PodcastRepository.get_recent_activity_rows(seeded, limit=10)
    assert sql_total == fb_total == 7
    by_id = lambda r: r["episode_id"]  # noqa: E731
    assert sorted(_normalize(sql_rows), key=by_id) == sorted(_normalize(fb_rows), key=by_id)
    stamps = [r["updated_at"] for r in _normalize(sql_rows)]
    assert stamps == sorted(stamps, reverse=True)

    page, _ = seeded.get_recent_activity_rows(limit=3, offset=2)
    assert len(page) == 3


def test_list_podcast_rows_matches_fallback(seeded):
    for kwargs in (
        {},
        {"q": "show"},
        {"q": "nyt"},  # author match, case-insensitive
        {"podcast_ids": [seeded.resolve_podcast_slug("beta-cast")]},
        {"limit": 1, "offset": 1},
        {"podcast_ids": []},
    ):
        sql_rows, sql_total = seeded.list_podcast_rows(**kwargs)
        fb_rows, fb_total = PodcastRepository.list_podcast_rows(seeded, **kwargs)
        assert sql_total == fb_total, kwargs
        assert _normalize(sql_rows) == _normalize(fb_rows), kwargs


def test_list_podcast_rows_counts(seeded):
    rows, _ = seeded.list_podcast_rows(q="beta")
    assert rows[0]["episodes_count"] == 3
    # cleaned + summarized; the failed episode is excluded from processed
    assert rows[0]["episodes_processed"] == 2


def test_get_podcast_row_by_slug_matches_fallback(seeded):
    sql = seeded.get_podcast_row_by_slug("beta-cast")
    fb = PodcastRepository.get_podcast_row_by_slug(seeded, "beta-cast")
    assert _normalize([sql]) == _normalize([fb])
    assert seeded.get_podcast_row_by_slug("missing") is None


def test_get_episodes_by_ids_matches_fallback(seeded):
    # Collect real episode ids via the corpus path.
    all_ids = [e.id for p in seeded.get_all() for e in p.episodes]
    wanted = all_ids[:3] + ["missing-id"]
    sql = seeded.get_episodes_by_ids(wanted)
    fb = EpisodeRepository.get_episodes_by_ids(seeded, wanted)
    assert set(sql.keys()) == set(fb.keys()) == set(all_ids[:3])
    for eid in sql:
        sql_podcast, sql_episode = sql[eid]
        fb_podcast, fb_episode = fb[eid]
        assert sql_episode.id == fb_episode.id
        assert sql_episode.title == fb_episode.title
        assert sql_podcast.id == fb_podcast.id
        assert sql_podcast.title == fb_podcast.title
    assert seeded.get_episodes_by_ids([]) == {}
