"""Where an episode enters the pipeline when its ``audio_url`` is not audio.

Incident 2026-09-21: a YouTube import was dead-lettered at TRANSCRIBE with
``[400] Unsupported content type: text/html`` because every "can we skip the
download?" decision was ``bool(audio_url)``, and a YouTube episode's
``audio_url`` is its watch page.
"""

from __future__ import annotations

import pytest

from thestill.core.queue_manager import TaskStage, starting_stage_for
from thestill.models.podcast import EpisodeState
from thestill.utils.url_patterns import is_remote_fetchable_audio_url


class TestIsRemoteFetchableAudioUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=7xTGNNLPyMI",
            "https://youtu.be/7xTGNNLPyMI",
            "https://m.youtube.com/watch?v=7xTGNNLPyMI&t=42",
        ],
    )
    def test_youtube_pages_are_not_fetchable_audio(self, url):
        assert is_remote_fetchable_audio_url(url) is False

    @pytest.mark.parametrize("url", [None, ""])
    def test_no_url_is_not_fetchable(self, url):
        assert is_remote_fetchable_audio_url(url) is False

    @pytest.mark.parametrize(
        "url",
        ["https://example.com/ep.mp3", "https://cdn.simplecast.com/audio/abc/episode.m4a?aid=rss_feed"],
    )
    def test_ordinary_enclosures_are(self, url):
        assert is_remote_fetchable_audio_url(url) is True


class TestStartingStage:
    def test_dalston_skips_download_only_for_a_fetchable_url(self):
        skip = starting_stage_for(
            EpisodeState.DISCOVERED, transcription_provider="dalston", has_fetchable_audio_url=True
        )
        youtube = starting_stage_for(
            EpisodeState.DISCOVERED, transcription_provider="dalston", has_fetchable_audio_url=False
        )
        assert skip == TaskStage.TRANSCRIBE
        assert youtube == TaskStage.DOWNLOAD

    def test_old_keyword_is_gone_so_no_caller_can_pass_bool_audio_url_by_habit(self):
        with pytest.raises(TypeError):
            starting_stage_for(EpisodeState.DISCOVERED, transcription_provider="dalston", has_audio_url=True)
