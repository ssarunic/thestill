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

"""Regression tests for spec #25, items 2.6 + 2.7 — audio integrity."""

import pytest

from thestill.utils.audio_integrity import InvalidAudioFile, assert_audio_file


@pytest.fixture
def tmp_audio(tmp_path):
    def _make(prefix: bytes, *, name: str = "x.mp3", extra_bytes: int = 64) -> "Path":
        path = tmp_path / name
        path.write_bytes(prefix + b"\x00" * extra_bytes)
        return path

    return _make


class TestAssertAudioFile:
    def test_mp3_id3_accepted(self, tmp_audio):
        path = tmp_audio(b"ID3\x04\x00\x00")
        assert assert_audio_file(path) == "mp3-id3"

    def test_mp3_frame_sync_accepted(self, tmp_audio):
        path = tmp_audio(b"\xff\xfb\x90\x00")
        assert assert_audio_file(path) == "mp3"

    def test_wav_accepted(self, tmp_audio):
        path = tmp_audio(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        assert assert_audio_file(path) == "wav"

    def test_ogg_accepted(self, tmp_audio):
        path = tmp_audio(b"OggS\x00")
        assert assert_audio_file(path) == "ogg"

    def test_flac_accepted(self, tmp_audio):
        path = tmp_audio(b"fLaC")
        assert assert_audio_file(path) == "flac"

    def test_mp4_family_accepted(self, tmp_audio):
        path = tmp_audio(b"\x00\x00\x00\x20ftypM4A ")
        assert assert_audio_file(path) == "mp4-family"

    def test_html_error_page_rejected(self, tmp_audio):
        """A podcast host returning an HTML error must not be mistaken for audio."""
        path = tmp_audio(b"<!DOCTYPE html><html><body>404</body></html>", name="fake.mp3")
        with pytest.raises(InvalidAudioFile):
            assert_audio_file(path)

    def test_zip_bomb_rejected(self, tmp_audio):
        """A ZIP polyglot must not get past the magic-byte gate."""
        path = tmp_audio(b"PK\x03\x04")
        with pytest.raises(InvalidAudioFile):
            assert_audio_file(path)

    def test_riff_without_wave_rejected(self, tmp_audio):
        """RIFF with AVI / WEBP payload - not WAVE - is refused."""
        path = tmp_audio(b"RIFF\x00\x00\x00\x00AVI LIST")
        with pytest.raises(InvalidAudioFile):
            assert_audio_file(path)

    def test_empty_file_rejected(self, tmp_path):
        path = tmp_path / "empty.mp3"
        path.write_bytes(b"")
        with pytest.raises(InvalidAudioFile):
            assert_audio_file(path)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(InvalidAudioFile):
            assert_audio_file(tmp_path / "does-not-exist.mp3")
