"""Spec #28 §2.3 — corpus_writer Markdown + segmap rendering."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

import pytest

from thestill.core.corpus_writer import CorpusWriter, _render_episode
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.models.entities import EntityRecord, EntityType
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.path_manager import PathManager


def _segment(seg_id: int, start: float, end: float, text: str, speaker: str | None = None) -> AnnotatedSegment:
    return AnnotatedSegment(
        id=seg_id,
        start=start,
        end=end,
        speaker=speaker,
        text=text,
        kind="content",
    )


def _transcript(*segments: AnnotatedSegment) -> AnnotatedTranscript:
    return AnnotatedTranscript(episode_id="ep-1", segments=list(segments))


def _podcast() -> Podcast:
    return Podcast(
        id=str(uuid.uuid4()),
        rss_url="https://example.com/feed.xml",
        title="Fixture Show",
        slug="fixture-show",
        description="",
    )


def _episode(podcast: Podcast) -> Episode:
    return Episode(
        id="11111111-2222-3333-4444-555555555555",
        external_id="e1",
        title="Sample Episode",
        description="",
        audio_url="https://example.com/ep1.mp3",
        pub_date=datetime(2026, 4, 28),
    )


class TestPureRender:
    def test_anchor_is_first_line_of_each_block(self):
        seg = _segment(0, 1.0, 5.0, "Hello world.", speaker="Host")
        body, segmap = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(seg),
            mention_links={},
        )
        # Find the segment block; its first line must be the anchor.
        idx = body.find("<!-- seg id=0")
        assert idx >= 0
        anchor_line_start = body.rfind("\n", 0, idx) + 1
        anchor_line = body[anchor_line_start : body.find("\n", idx)]
        assert anchor_line.startswith("<!-- seg id=0 ")

    def test_segmap_byte_offsets_match_rendered_file(self):
        s1 = _segment(0, 1.0, 5.0, "First segment.", speaker="A")
        s2 = _segment(1, 5.0, 10.0, "Second segment.", speaker="B")
        body, segmap = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(s1, s2),
            mention_links={},
        )
        encoded = body.encode("utf-8")
        for entry in segmap:
            chunk = encoded[entry.byte_start : entry.byte_end]
            # The chunk must start with the anchor for that segment.
            assert chunk.startswith(
                f"<!-- seg id={entry.seg_id}".encode("utf-8")
            ), f"byte_start at {entry.byte_start} does not anchor seg {entry.seg_id}"

    def test_segmap_line_numbers_match_rendered_file(self):
        s1 = _segment(0, 1.0, 5.0, "First.")
        s2 = _segment(1, 5.0, 10.0, "Second.")
        body, segmap = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(s1, s2),
            mention_links={},
        )
        lines = body.split("\n")
        for entry in segmap:
            line_text = lines[entry.line_start - 1]  # 1-indexed
            assert line_text.startswith(
                f"<!-- seg id={entry.seg_id}"
            ), f"line {entry.line_start} ({line_text!r}) is not seg {entry.seg_id}'s anchor"

    def test_wikilinks_rendered_inline(self):
        seg = _segment(7, 0.0, 5.0, "We talked about it.", speaker="Host")
        body, _ = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(seg),
            mention_links={7: ["person:elon-musk", "company:spacex"]},
        )
        assert "[[person:elon-musk]]" in body
        assert "[[company:spacex]]" in body

    def test_drops_non_content_segments(self):
        ad = AnnotatedSegment(id=0, start=0.0, end=10.0, text="Sponsor read", kind="ad_break")
        content = _segment(1, 10.0, 20.0, "Real content.")
        body, segmap = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(ad, content),
            mention_links={},
        )
        assert "Sponsor read" not in body
        assert len(segmap) == 1
        assert segmap[0].seg_id == 1

    def test_empty_transcript_emits_frontmatter_only(self):
        body, segmap = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(),
            mention_links={},
        )
        assert segmap == []
        assert body.startswith("---\n")
        # No segment anchors
        assert "<!-- seg id=" not in body

    def test_speaker_with_double_quotes_is_sanitised(self):
        seg = _segment(0, 0.0, 1.0, "Hi.", speaker='Bob "the Boss" Smith')
        body, _ = _render_episode(
            podcast=_podcast(),
            episode=_episode(_podcast()),
            transcript=_transcript(seg),
            mention_links={},
        )
        # No raw double-quote inside the anchor; comment terminator
        # ``-->`` must still close the anchor cleanly.
        anchor_line = next(line for line in body.split("\n") if line.startswith("<!-- seg id=0"))
        assert anchor_line.endswith("-->")
        assert anchor_line.count('"') == 2  # only the surrounding spk="..."


class TestIdempotence:
    def test_rerendering_same_transcript_is_byte_identical(self):
        # Reuse the SAME podcast/episode pair so the rendered IDs match.
        # _podcast() generates a fresh UUID per call by design.
        podcast = _podcast()
        episode = _episode(podcast)
        seg = _segment(3, 12.5, 17.0, "Idempotent rendering.", speaker="X")
        b1, _ = _render_episode(
            podcast=podcast,
            episode=episode,
            transcript=_transcript(seg),
            mention_links={3: ["person:foo"]},
        )
        b2, _ = _render_episode(
            podcast=podcast,
            episode=episode,
            transcript=_transcript(seg),
            mention_links={3: ["person:foo"]},
        )
        assert b1 == b2


class TestWriteToDisk:
    def test_write_episode_page_writes_markdown_and_segmap(self, tmp_path):
        # Set up minimal podcasts.db so SqliteEntityRepository can run.
        SqlitePodcastRepository(db_path=str(tmp_path / "podcasts.db"))
        repo = SqliteEntityRepository(db_path=str(tmp_path / "podcasts.db"))
        path_manager = PathManager(storage_path=str(tmp_path))

        writer = CorpusWriter(path_manager=path_manager, entity_repository=repo)

        podcast = _podcast()
        # Insert a podcast row so PathManager.corpus_episode_file works
        with sqlite3.connect(str(tmp_path / "podcasts.db")) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast.id, str(podcast.rss_url), podcast.title, podcast.slug),
            )
            conn.commit()

        episode = _episode(podcast)
        transcript = _transcript(_segment(0, 1.0, 5.0, "Hello.", speaker="Host"))
        result = writer.write_episode_page(
            podcast=podcast,
            episode=episode,
            transcript=transcript,
            mention_links={},
        )
        md_path = path_manager.corpus_episode_file(podcast.slug, episode.id)
        seg_path = path_manager.corpus_episode_segmap_file(podcast.slug, episode.id)
        assert md_path.exists()
        assert seg_path.exists()
        assert md_path in result.written
        assert seg_path in result.written

        # Sidecar parses as JSON with the expected shape
        rows = json.loads(seg_path.read_text())
        assert len(rows) == 1
        assert rows[0]["seg_id"] == 0
        assert rows[0]["start_ms"] == 1000
        assert rows[0]["end_ms"] == 5000

    def test_unchanged_rerun_does_not_rewrite(self, tmp_path):
        SqlitePodcastRepository(db_path=str(tmp_path / "podcasts.db"))
        repo = SqliteEntityRepository(db_path=str(tmp_path / "podcasts.db"))
        path_manager = PathManager(storage_path=str(tmp_path))
        writer = CorpusWriter(path_manager=path_manager, entity_repository=repo)
        podcast = _podcast()
        with sqlite3.connect(str(tmp_path / "podcasts.db")) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast.id, str(podcast.rss_url), podcast.title, podcast.slug),
            )
            conn.commit()
        episode = _episode(podcast)
        transcript = _transcript(_segment(0, 1.0, 5.0, "Same.", speaker="Host"))

        first = writer.write_episode_page(podcast=podcast, episode=episode, transcript=transcript, mention_links={})
        assert len(first.written) == 2
        second = writer.write_episode_page(podcast=podcast, episode=episode, transcript=transcript, mention_links={})
        assert second.written == []
        assert len(second.skipped_unchanged) == 2


class TestEntityPages:
    def test_person_entity_page_rendered(self, tmp_path):
        SqlitePodcastRepository(db_path=str(tmp_path / "podcasts.db"))
        repo = SqliteEntityRepository(db_path=str(tmp_path / "podcasts.db"))
        path_manager = PathManager(storage_path=str(tmp_path))
        writer = CorpusWriter(path_manager=path_manager, entity_repository=repo)
        repo.upsert_entity(
            EntityRecord(
                id="person:elon-musk",
                type=EntityType.PERSON,
                canonical_name="Elon Musk",
                wikidata_qid="Q317521",
                aliases=["Musk"],
            )
        )
        result = writer.write_entity_page(repo.get_entity("person:elon-musk"))
        page = path_manager.corpus_entity_file("person", "elon-musk")
        assert page.exists()
        body = page.read_text()
        assert "Elon Musk" in body
        assert "Q317521" in body
        assert "Musk" in body  # alias
        assert page in result.written

    def test_product_entities_skipped(self, tmp_path):
        SqlitePodcastRepository(db_path=str(tmp_path / "podcasts.db"))
        repo = SqliteEntityRepository(db_path=str(tmp_path / "podcasts.db"))
        path_manager = PathManager(storage_path=str(tmp_path))
        writer = CorpusWriter(path_manager=path_manager, entity_repository=repo)
        product = EntityRecord(
            id="product:tesla-roadster",
            type=EntityType.PRODUCT,
            canonical_name="Tesla Roadster",
        )
        result = writer.write_entity_page(product)
        assert result.written == []
        assert result.skipped_unchanged == []
