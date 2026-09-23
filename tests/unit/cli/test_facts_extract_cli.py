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

"""``thestill facts extract --episode-id <uuid>`` resolves the episode.

``get_episode`` returns ``(podcast, episode)`` on both backends; the
command used to keep the tuple and fail on ``episode.title``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.models.facts import EpisodeFacts, PodcastFacts
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

RSS_URL = "https://example.com/f.xml"


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    (storage / "raw_transcripts" / "show").mkdir(parents=True)
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    transcript = storage / "raw_transcripts" / "show" / "ep_transcript.json"
    transcript.write_text(json.dumps({"segments": [{"text": "hi", "start": 0.0, "end": 1.0}]}), encoding="utf-8")

    db_path = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    podcast_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, 'Show', 'show')",
            (podcast_id, RSS_URL),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, raw_transcript_path) "
            "VALUES (?, ?, 'e1', 'Ep', 'ep', 'https://x/e.mp3', 'show/ep_transcript.json')",
            (episode_id, podcast_id),
        )
    return storage, episode_id


def _stub_extraction(monkeypatch):
    monkeypatch.setattr("thestill.cli.create_llm_provider_from_config", lambda config: MagicMock())
    monkeypatch.setattr(
        "thestill.core.facts_extractor.FactsExtractor.extract_episode_facts",
        lambda self, **kw: EpisodeFacts(episode_title=kw["episode_title"], speaker_mapping={"SPEAKER_00": "Host 1"}),
    )
    monkeypatch.setattr(
        "thestill.core.facts_extractor.FactsExtractor.extract_initial_podcast_facts",
        lambda self, **kw: PodcastFacts(podcast_title=kw["podcast_title"]),
    )


def test_an_episode_given_by_uuid_is_extracted(cli_db, monkeypatch):
    storage, episode_id = cli_db
    _stub_extraction(monkeypatch)

    result = CliRunner().invoke(main, ["facts", "extract", "--podcast-id", RSS_URL, "--episode-id", episode_id])

    assert result.exit_code == 0, result.output
    assert "🎧 Ep" in result.output
    facts_file = storage / "episode_facts" / "show" / "ep.facts.md"
    assert facts_file.exists() and "Host 1" in facts_file.read_text(encoding="utf-8")


def test_an_unknown_episode_uuid_is_reported(cli_db, monkeypatch):
    _, _ = cli_db
    _stub_extraction(monkeypatch)

    result = CliRunner().invoke(main, ["facts", "extract", "--podcast-id", RSS_URL, "--episode-id", str(uuid.uuid4())])

    assert result.exit_code == 1
    assert "No episode with transcript found" in result.output
