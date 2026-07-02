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

"""LLM-output control-character sanitizer (the sauté-NUL incident)."""

from __future__ import annotations

from thestill.core.segmented_transcript_cleaner import CleanupPatch
from thestill.utils.text_sanitizer import sanitize_text


class TestSanitizeText:
    def test_strips_the_incident_nul(self):
        clean, removed = sanitize_text("It does things like saut\x00 onions.")
        assert clean == "It does things like saut onions."
        assert removed == 1

    def test_strips_c0_and_c1_ranges(self):
        dirty = "a\x01b\x08c\x0bd\x0ce\x0ef\x1fg\x7fh\x85i\x9cj"
        clean, removed = sanitize_text(dirty)
        assert clean == "abcdefghij"
        assert removed == 9

    def test_preserves_tab_newline_carriage_return(self):
        text = "line one\nline two\r\n\tindented"
        clean, removed = sanitize_text(text)
        assert clean == text
        assert removed == 0

    def test_preserves_natural_transcript_content(self):
        """Everything that legitimately appears in cleaned transcripts must
        survive byte-identical: accents, CJK, emoji, curly quotes, dashes,
        markdown syntax, currency, math symbols."""
        text = (
            "Sauté onions — that's café-quality! «Voilà»… "
            "München, São Paulo, Zürich; 東京 and 北京. "
            "Emoji: 🎧🚀🤖 fine. Curly “quotes” and ‘apostrophes’. "
            "Markdown: **bold**, `code`, [link](https://x.y), > quote, | table |. "
            "Symbols: €100, £50, ¥200, 50°C, ±5%, x² ≠ y³, α/β/γ."
        )
        clean, removed = sanitize_text(text)
        assert clean == text
        assert removed == 0

    def test_empty_and_clean_strings_pass_through(self):
        assert sanitize_text("") == ("", 0)
        assert sanitize_text("plain ascii") == ("plain ascii", 0)


class TestCleanupPatchValidator:
    def test_patch_scrubs_nul_from_cleaned_text(self):
        patch = CleanupPatch(id=1, cleaned_text="saut\x00 onions", kind="content")
        assert patch.cleaned_text == "saut onions"

    def test_patch_scrubs_sponsor_too(self):
        patch = CleanupPatch(id=1, cleaned_text="ad copy", kind="ad_break", sponsor="Acme\x00 Corp")
        assert patch.sponsor == "Acme Corp"

    def test_patch_preserves_clean_text_and_none_sponsor(self):
        patch = CleanupPatch(id=1, cleaned_text="Sauté onions — 東京 🎧\nnew line", kind="content")
        assert patch.cleaned_text == "Sauté onions — 東京 🎧\nnew line"
        assert patch.sponsor is None
