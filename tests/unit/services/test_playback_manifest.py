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

"""Spec #61 §4 — playback-asset manifest builder (+ spec #62 youtube asset)."""

import pytest

from thestill.models.podcast import AlternateEnclosure, Episode
from thestill.services.playback import build_playback_manifest, is_video_enclosure
from thestill.utils.url_patterns import extract_youtube_video_id


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


def _yt_alt(uri: str, **overrides) -> AlternateEnclosure:
    defaults = dict(source_uri=uri, mime_type="video/youtube")
    defaults.update(overrides)
    return AlternateEnclosure(**defaults)


class TestExtractYoutubeVideoId:
    """Spec #62 §4 — untrusted-feed video-id extraction + 11-char validation."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://youtu.be/6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://www.youtube.com/watch?v=6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://youtube.com/watch?list=PL123&v=6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://m.youtube.com/watch?v=6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://www.youtube.com/embed/6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://www.youtube.com/shorts/6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://www.youtube-nocookie.com/embed/6wz3LrdMnVo", "6wz3LrdMnVo"),
            ("https://youtu.be/6wz3LrdMnVo?t=42", "6wz3LrdMnVo"),
            # Wrong length (10 and 12 chars) fails the anchored validation.
            ("https://youtu.be/6wz3LrdMnV", None),
            ("https://youtu.be/6wz3LrdMnVoX", None),
            # Hostile / malformed input never yields an id, never raises.
            ("https://youtu.be/<script>bad", None),
            ("https://evil.example.com/watch?v=6wz3LrdMnVo", None),
            ("https://youtube.com.evil.example/watch?v=6wz3LrdMnVo", None),
            ("javascript:alert(1)", None),
            ("not a url at all", None),
            ("https://www.youtube.com/watch", None),
            ("", None),
        ],
    )
    def test_extraction_table(self, url, expected):
        assert extract_youtube_video_id(url) == expected


class TestYoutubeAsset:
    """Spec #62 §4 — youtube asset selection in the manifest."""

    def test_audio_kind_episode_with_youtube_link(self):
        """The real corpus shape: audio enclosure + video/youtube alt-enclosure."""
        episode = _episode(image_url="https://example.com/art.jpg")
        manifest = build_playback_manifest(episode, [_yt_alt("https://youtu.be/6wz3LrdMnVo", title="Ep title")])

        assert manifest["kind"] == "audio"  # kind ignores the youtube asset
        assert manifest["audio"] is not None
        assert manifest["youtube"] == {
            "video_id": "6wz3LrdMnVo",
            "watch_url": "https://www.youtube.com/watch?v=6wz3LrdMnVo",
            "title": "Ep title",
        }
        # Theater needs a poster even for an audio-kind episode once a
        # youtube rendition exists.
        assert manifest["poster_url"] == "https://example.com/art.jpg"
        # The youtube asset carries NO timeline_offset by design (§8).
        assert "timeline_offset" not in manifest["youtube"]

    def test_no_alternate_enclosures_emits_null(self):
        manifest = build_playback_manifest(_episode())
        assert manifest["youtube"] is None
        assert manifest["poster_url"] is None

    def test_default_entry_wins_over_insertion_order(self):
        alts = [
            _yt_alt("https://youtu.be/firstVID001"),
            _yt_alt("https://youtu.be/deflt2VID02", is_default=True),
        ]
        manifest = build_playback_manifest(_episode(), alts)
        assert manifest["youtube"]["video_id"] == "deflt2VID02"

    def test_insertion_order_tiebreak_without_default(self):
        alts = [
            _yt_alt("https://youtu.be/firstVID001"),
            _yt_alt("https://youtu.be/secndVID002"),
        ]
        manifest = build_playback_manifest(_episode(), alts)
        assert manifest["youtube"]["video_id"] == "firstVID001"

    def test_invalid_video_id_skipped_to_next_candidate(self):
        alts = [
            _yt_alt("https://youtu.be/short", is_default=True),  # invalid id, skipped
            _yt_alt("https://youtu.be/validVID003"),
        ]
        manifest = build_playback_manifest(_episode(), alts)
        assert manifest["youtube"]["video_id"] == "validVID003"

    def test_all_invalid_emits_null(self):
        manifest = build_playback_manifest(_episode(), [_yt_alt("https://example.com/not-youtube")])
        assert manifest["youtube"] is None

    def test_non_youtube_mimes_ignored(self):
        alts = [_yt_alt("https://cdn.example.com/720.mp4", mime_type="video/mp4")]
        manifest = build_playback_manifest(_episode(), alts)
        assert manifest["youtube"] is None

    def test_video_kind_episode_can_also_carry_youtube(self):
        episode = _episode(
            audio_url="https://example.com/ep.mp4",
            audio_mime_type="video/mp4",
            image_url="https://example.com/art.jpg",
        )
        manifest = build_playback_manifest(episode, [_yt_alt("https://youtu.be/6wz3LrdMnVo")])
        assert manifest["kind"] == "video"
        assert manifest["youtube"]["video_id"] == "6wz3LrdMnVo"


HLS = "https://episode.flightcast.com/hls/v/01M6FYA4GH9W4DYQQG4WV857SE.m3u8"


def _video_alt(uri: str = HLS, **overrides) -> AlternateEnclosure:
    defaults = dict(source_uri=uri, mime_type="application/x-mpegURL", title="HLS Video Stream")
    defaults.update(overrides)
    return AlternateEnclosure(**defaults)


class TestNativeVideoAlternate:
    """Spec #113 — HLS / progressive video alternates become the video rendition."""

    def test_hls_alternate_makes_video_kind_and_keeps_audio(self):
        """The Flightcast shape: MP3 enclosure + HLS alternate enclosure."""
        episode = _episode(
            audio_mime_type="audio/mpeg",
            duration=3473,
            image_url="https://example.com/art.jpg",
            playback_time_offset_seconds=0.5,
        )
        manifest = build_playback_manifest(episode, [_video_alt(height=1080)])

        assert manifest["kind"] == "video"
        assert manifest["audio"] == {
            "url": "https://example.com/ep.mp3",
            "mime_type": "audio/mpeg",
            "duration": 3473,
            "timeline_offset": 0.5,
        }
        assert manifest["video"] == {
            "url": HLS,
            "mime_type": "application/x-mpegURL",
            "duration": 3473,
            "timeline_offset": 0.5,  # shares the episode's offset (drift not corrected)
            "height": 1080,
        }
        assert manifest["poster_url"] == "https://example.com/art.jpg"
        assert manifest["youtube"] is None

    def test_apple_mime_spelling_and_case_accepted(self):
        manifest = build_playback_manifest(_episode(), [_video_alt(mime_type="APPLICATION/VND.APPLE.MPEGURL")])
        assert manifest["kind"] == "video"
        assert manifest["video"]["url"] == HLS

    def test_progressive_video_alternate(self):
        manifest = build_playback_manifest(
            _episode(), [_video_alt("https://cdn.example.com/720.mp4", mime_type="video/mp4")]
        )
        assert manifest["kind"] == "video"
        assert manifest["video"]["url"] == "https://cdn.example.com/720.mp4"
        assert manifest["youtube"] is None

    def test_default_wins_then_hls_beats_progressive(self):
        mp4 = _video_alt("https://cdn.example.com/720.mp4", mime_type="video/mp4")
        assert build_playback_manifest(_episode(), [mp4, _video_alt()])["video"]["url"] == HLS

        default_mp4 = _video_alt("https://cdn.example.com/720.mp4", mime_type="video/mp4", is_default=True)
        assert (
            build_playback_manifest(_episode(), [_video_alt(), default_mp4])["video"]["url"]
            == "https://cdn.example.com/720.mp4"
        )

    @pytest.mark.parametrize(
        "uri",
        [
            "http://episode.flightcast.com/v.m3u8",  # mixed content
            "ipfs://bafybeigdyrzt/v.m3u8",
            "https:///no-host.m3u8",
            "https://[unbalanced/v.m3u8",
            "javascript:alert(1)",
        ],
    )
    def test_untrusted_uri_skipped_to_next_candidate(self, uri):
        manifest = build_playback_manifest(_episode(), [_video_alt(uri, is_default=True), _video_alt()])
        assert manifest["video"]["url"] == HLS

    def test_all_invalid_falls_back_to_audio_kind(self):
        manifest = build_playback_manifest(_episode(), [_video_alt("http://insecure.example.com/v.m3u8")])
        assert manifest["kind"] == "audio"
        assert manifest["video"] is None

    def test_youtube_alternate_is_not_a_native_video(self):
        manifest = build_playback_manifest(_episode(), [_yt_alt("https://youtu.be/6wz3LrdMnVo")])
        assert manifest["kind"] == "audio"
        assert manifest["video"] is None

    def test_hls_and_youtube_alternates_coexist(self):
        alts = [_yt_alt("https://youtu.be/6wz3LrdMnVo"), _video_alt()]
        manifest = build_playback_manifest(_episode(), alts)
        assert manifest["kind"] == "video"
        assert manifest["video"]["url"] == HLS
        assert manifest["youtube"]["video_id"] == "6wz3LrdMnVo"

    def test_video_enclosure_ignores_video_alternates(self):
        episode = _episode(audio_url="https://example.com/ep.mp4", audio_mime_type="video/mp4")
        manifest = build_playback_manifest(episode, [_video_alt()])
        assert manifest["kind"] == "video"
        assert manifest["audio"] is None
        assert manifest["video"]["url"] == "https://example.com/ep.mp4"
