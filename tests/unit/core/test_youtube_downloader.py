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

"""YouTubeDownloader failure path: the error handler must survive any exception."""

from datetime import datetime, timezone

import yt_dlp

from thestill.core import youtube_downloader as yd
from thestill.models.podcast import Episode
from thestill.utils.youtube_errors import YOUTUBE_BOT_CHECK_MESSAGE


class _FailingYoutubeDL:
    def __init__(self, message: str) -> None:
        self._message = message

    def __call__(self, opts):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        raise yt_dlp.utils.DownloadError(self._message)


def _episode() -> Episode:
    return Episode(
        external_id="abc123",
        podcast_id="p1",
        title="An episode",
        description="",
        pub_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        audio_url="https://www.youtube.com/watch?v=abc123",  # type: ignore[arg-type]
    )


def test_download_failure_returns_none_and_keeps_actionable_reason(tmp_path, monkeypatch):
    """A bot-check failure must surface its message, not an AttributeError from the handler."""
    raw = "ERROR: [youtube] abc123: Sign in to confirm you're not a bot. Use --cookies-from-browser"
    monkeypatch.setattr(yd.yt_dlp, "YoutubeDL", _FailingYoutubeDL(raw))
    downloader = yd.YouTubeDownloader(storage_path=str(tmp_path))

    assert downloader.download_episode(_episode(), "Podcast") is None
    assert downloader.last_error == YOUTUBE_BOT_CHECK_MESSAGE


def test_download_failure_falls_back_to_raw_text_for_unknown_errors(tmp_path, monkeypatch):
    raw = "ERROR: something new and strange"
    monkeypatch.setattr(yd.yt_dlp, "YoutubeDL", _FailingYoutubeDL(raw))
    downloader = yd.YouTubeDownloader(storage_path=str(tmp_path))

    assert downloader.download_episode(_episode(), "Podcast") is None
    assert downloader.last_error == raw
