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

"""Shared fixtures for service-layer unit tests."""

import pytest


# Minimal yt-dlp output shape for a single video. Real responses include many
# more keys; the YouTubeResolver only consults the ones below.
@pytest.fixture
def fake_youtube_video_info():
    return {
        "id": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "description": "Music video",
        "channel": "Rick Astley",
        "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
        "uploader": "Rick Astley",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "duration": 213,
        "upload_date": "20091025",
        "thumbnails": [
            {"url": "https://i.ytimg.com/lo.jpg"},
            {"url": "https://i.ytimg.com/hi.jpg"},
        ],
    }
