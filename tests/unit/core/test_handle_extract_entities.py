"""Spec #28 §1.3 — handle_extract_entities task handler.

Tests:

- happy path: episode with sidecar → extractor runs → mentions persisted
  → status flips ``pending`` → ``complete``
- skipped_legacy: episode without sidecar → status flips to
  ``skipped_legacy``, no mentions written
- missing sidecar file (DB says path X, disk doesn't have it) → FatalError
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from thestill.core.entity_extractor import EntityExtractor
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_handlers import handle_extract_entities
from thestill.models.entities import EntityMention, ResolutionStatus
from thestill.models.podcast import Episode, Podcast
from thestill.utils.exceptions import FatalError

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "entity_extractor" / "sample_episode_okrs.json"


class StubGLiNER:
    """Local copy of the stub used in ``test_entity_extractor.py``.

    Pytest's ``conftest.py`` doesn't share importable classes (only
    fixtures), and this project doesn't use ``__init__.py`` in the
    tests tree, so the alternative is restructuring or duplication.
    Duplication wins.
    """

    def predict_entities(self, text: str, labels: List[str], threshold: float = 0.5):
        if "OKR" in text:
            idx = text.find("OKR")
            return [{"text": "OKR", "label": "topic", "start": idx, "end": idx + 3, "score": 0.9}]
        return []

    def inference(self, texts, labels: List[str], threshold: float = 0.5, **_):
        if isinstance(texts, str):
            return self.predict_entities(texts, labels, threshold)
        return [self.predict_entities(t, labels, threshold) for t in texts]


def _build_state(tmp_path, episode, podcast, sidecar_relpath: str | None):
    """Build a MagicMock AppState with the minimum surface the handler uses."""
    state = MagicMock()

    if sidecar_relpath:
        full = tmp_path / sidecar_relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(FIXTURE.read_text(), encoding="utf-8")

    # The handler resolves the sidecar via
    # ``path_manager.clean_transcript_file(rel_path)``, which on the
    # real PathManager joins under ``clean_transcripts_dir``. Mock it
    # to return the matching tmp_path-relative file.
    def _clean_transcript_file(rel: str):
        return tmp_path / rel

    state.path_manager.clean_transcript_file.side_effect = _clean_transcript_file
    state.repository.get_episode.return_value = (podcast, episode)
    state.entity_extractor = EntityExtractor(preloaded_model=StubGLiNER())
    return state


def _make_task() -> Task:
    return Task(
        id=str(uuid.uuid4()),
        episode_id="ep-uuid",
        stage=TaskStage.EXTRACT_ENTITIES,
        status=TaskStatus.PROCESSING,
    )


def _make_podcast() -> Podcast:
    return Podcast(
        id=str(uuid.uuid4()),
        rss_url="https://example.com/feed.xml",
        title="Fixture",
        slug="fixture",
        description="",
    )


def _make_episode(*, json_path: str | None) -> Episode:
    return Episode(
        id="ep-uuid",
        external_id="e1",
        title="Fixture Ep",
        description="",
        audio_url="https://example.com/ep1.mp3",
        clean_transcript_path="fixture/ep_cleaned.md",
        clean_transcript_json_path=json_path,
    )


class TestHappyPath:
    def test_runs_extractor_and_persists_mentions(self, tmp_path):
        episode = _make_episode(json_path="fixture/ep_cleaned.json")
        state = _build_state(tmp_path, episode, _make_podcast(), "fixture/ep_cleaned.json")

        handle_extract_entities(_make_task(), state)

        # Status writes: first 'pending', then 'complete'.
        statuses = [call.kwargs["status"] for call in state.repository.update_entity_extraction_status.call_args_list]
        assert statuses == ["pending", "complete"]

        # Idempotent wipe + insert.
        state.entity_repository.delete_mentions_for_episode.assert_called_once_with("ep-uuid")
        insert_call = state.entity_repository.insert_mentions.call_args
        mentions = list(insert_call.args[0])
        assert len(mentions) > 0
        for m in mentions:
            assert isinstance(m, EntityMention)
            assert m.entity_id is None
            assert m.resolution_status is ResolutionStatus.PENDING
            assert m.episode_id == "ep-uuid"


class TestSkippedLegacy:
    def test_no_sidecar_means_skipped_legacy(self, tmp_path):
        episode = _make_episode(json_path=None)  # legacy: only Markdown, no JSON
        state = _build_state(tmp_path, episode, _make_podcast(), sidecar_relpath=None)

        handle_extract_entities(_make_task(), state)

        state.repository.update_entity_extraction_status.assert_called_once_with(
            episode_id="ep-uuid",
            status="skipped_legacy",
        )
        # No extractor work, no mention writes.
        state.entity_repository.delete_mentions_for_episode.assert_not_called()
        state.entity_repository.insert_mentions.assert_not_called()


class TestMissingSidecarFile:
    def test_db_says_present_but_disk_missing_raises_fatal(self, tmp_path):
        episode = _make_episode(json_path="fixture/missing.json")
        # Build state but DON'T create the sidecar file.
        state = _build_state(tmp_path, episode, _make_podcast(), sidecar_relpath=None)

        with pytest.raises(FatalError) as exc_info:
            handle_extract_entities(_make_task(), state)
        assert "sidecar not found" in str(exc_info.value)
