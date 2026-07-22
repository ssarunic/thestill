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

"""Spec #61 §4 — playback-asset manifest builder."""

from thestill.models.podcast import Episode
from thestill.services.playback import build_playback_manifest, is_video_enclosure


def _episode(**overrides) -> Episode:
    defaults = dict(
        external_id="guid-1",
        title="Test Episode",
        description="desc",
        audio_url="https://example.com/ep.mp3",
    )
    defaults.update(overrides)
    return Episode(**defaults)


class TestIsVideoEnclosure:
    def test_video_mime_types(self):
        assert is_video_enclosure("video/mp4")
        assert is_video_enclosure("VIDEO/MP4")
        assert is_video_enclosure("video/quicktime")

    def test_audio_and_absent_mime_types(self):
        assert not is_video_enclosure("audio/mpeg")
        assert not is_video_enclosure("audio/mp4")
        assert not is_video_enclosure(None)
        assert not is_video_enclosure("")


class TestBuildPlaybackManifest:
    def test_audio_episode_emits_audio_kind_with_existing_url(self):
        episode = _episode(
            audio_mime_type="audio/mpeg",
            duration=3600,
            playback_time_offset_seconds=1.5,
        )
        manifest = build_playback_manifest(episode)

        assert manifest["kind"] == "audio"
        assert manifest["video"] is None
        assert manifest["audio"] == {
            "url": "https://example.com/ep.mp3",
            "mime_type": "audio/mpeg",
            "duration": 3600,
            "timeline_offset": 1.5,
        }
        assert manifest["poster_url"] is None
        assert manifest["captions_url"] is None

    def test_missing_mime_type_defaults_to_audio(self):
        manifest = build_playback_manifest(_episode(audio_mime_type=None))
        assert manifest["kind"] == "audio"
        assert manifest["audio"]["url"] == "https://example.com/ep.mp3"

    def test_video_enclosure_emits_video_kind_with_poster(self):
        episode = _episode(
            audio_url="https://example.com/ep.mp4",
            audio_mime_type="video/mp4",
            duration=1800,
            image_url="https://example.com/art.jpg",
            playback_time_offset_seconds=0.0,
        )
        manifest = build_playback_manifest(episode)

        assert manifest["kind"] == "video"
        assert manifest["audio"] is None
        assert manifest["video"] == {
            "url": "https://example.com/ep.mp4",
            "mime_type": "video/mp4",
            "duration": 1800,
            "timeline_offset": 0.0,
        }
        assert manifest["poster_url"] == "https://example.com/art.jpg"

    def test_video_asset_carries_timeline_offset(self):
        episode = _episode(
            audio_url="https://example.com/ep.mp4",
            audio_mime_type="video/mp4",
            playback_time_offset_seconds=12.25,
        )
        manifest = build_playback_manifest(episode)
        assert manifest["video"]["timeline_offset"] == 12.25
