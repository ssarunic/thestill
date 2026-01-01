# Copyright 2025 thestill.me
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

"""Tests for HTML utility functions."""

import pytest

from thestill.utils.html_utils import extract_links_from_html, html_to_plain_text


class TestHtmlToPlainText:
    """Tests for html_to_plain_text function."""

    def test_none_input(self):
        """Test that None input returns empty string."""
        assert html_to_plain_text(None) == ""

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert html_to_plain_text("") == ""

    def test_plain_text_passthrough(self):
        """Test that plain text without HTML passes through unchanged."""
        text = "This is plain text without any HTML tags."
        assert html_to_plain_text(text) == text

    def test_paragraph_tags(self):
        """Test that paragraph tags are converted to double newlines."""
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        result = html_to_plain_text(html)
        assert result == "First paragraph.\n\nSecond paragraph."

    def test_paragraph_with_whitespace(self):
        """Test paragraph tags with whitespace between them."""
        html = "<p>First.</p>  \n  <p>Second.</p>"
        result = html_to_plain_text(html)
        assert result == "First.\n\nSecond."

    def test_line_breaks(self):
        """Test that br tags are converted to newlines."""
        html = "Line one<br>Line two<br/>Line three<br />"
        result = html_to_plain_text(html)
        assert "Line one\nLine two\nLine three" in result

    def test_unordered_list(self):
        """Test that unordered lists are converted to bullet points."""
        html = "<ul><li>Item one</li><li>Item two</li></ul>"
        result = html_to_plain_text(html)
        assert "- Item one" in result
        assert "- Item two" in result

    def test_ordered_list(self):
        """Test that ordered lists are also converted to bullet points."""
        html = "<ol><li>First</li><li>Second</li></ol>"
        result = html_to_plain_text(html)
        assert "- First" in result
        assert "- Second" in result

    def test_simple_link(self):
        """Test that links are converted to text (url) format."""
        html = '<a href="https://example.com">Click here</a>'
        result = html_to_plain_text(html)
        assert result == "Click here (https://example.com)"

    def test_link_text_is_url(self):
        """Test that when link text equals URL, only URL is shown."""
        html = '<a href="https://example.com">https://example.com</a>'
        result = html_to_plain_text(html)
        assert result == "https://example.com"

    def test_link_with_nested_tags(self):
        """Test links with nested formatting tags."""
        html = '<a href="https://example.com"><strong>Bold link</strong></a>'
        result = html_to_plain_text(html)
        assert result == "Bold link (https://example.com)"

    def test_javascript_link_ignored(self):
        """Test that javascript: links are handled gracefully."""
        html = '<a href="javascript:void(0)">Click me</a>'
        result = html_to_plain_text(html)
        assert result == "Click me"

    def test_bold_and_strong_tags(self):
        """Test that bold/strong tags are removed but content preserved."""
        html = "<p><strong>Important:</strong> This is <b>bold</b> text.</p>"
        result = html_to_plain_text(html)
        assert result == "Important: This is bold text."

    def test_italic_and_em_tags(self):
        """Test that italic/em tags are removed but content preserved."""
        html = "<p><em>Emphasized</em> and <i>italic</i> text.</p>"
        result = html_to_plain_text(html)
        assert result == "Emphasized and italic text."

    def test_horizontal_rule(self):
        """Test that hr tags are converted to dashes."""
        html = "<p>Section one</p><hr><p>Section two</p>"
        result = html_to_plain_text(html)
        assert "---" in result

    def test_headings(self):
        """Test that heading tags add newlines."""
        html = "<h1>Title</h1><p>Content</p>"
        result = html_to_plain_text(html)
        assert "Title" in result
        assert "Content" in result

    def test_html_entities(self):
        """Test that HTML entities are decoded."""
        html = "<p>R&amp;D department &lt;3 coding &gt; testing</p>"
        result = html_to_plain_text(html)
        assert "R&D department <3 coding > testing" in result

    def test_multiple_spaces_collapsed(self):
        """Test that multiple spaces are collapsed to single space."""
        html = "<p>Too    many     spaces</p>"
        result = html_to_plain_text(html)
        assert result == "Too many spaces"

    def test_multiple_newlines_collapsed(self):
        """Test that more than 2 consecutive newlines are collapsed."""
        html = "<p>First</p>\n\n\n\n<p>Second</p>"
        result = html_to_plain_text(html)
        # Should have at most 2 newlines between paragraphs
        assert "\n\n\n" not in result

    def test_leading_trailing_whitespace_removed(self):
        """Test that leading/trailing whitespace is removed."""
        html = "  <p>  Content  </p>  "
        result = html_to_plain_text(html)
        assert result == "Content"

    def test_real_world_rss_description(self):
        """Test with a real-world RSS feed description."""
        html = """<p><strong>Matt MacInnis</strong> is the CPO at Rippling.</p>
        <p><strong>We discuss:</strong></p>
        <p>1. Why extraordinary results demand extraordinary efforts</p>
        <p>—</p>
        <p><strong>Brought to you by:</strong></p>
        <p><a href="https://ai.dev/" target="_blank"><strong>Google Gemini</strong></a>—Your AI assistant</p>"""

        result = html_to_plain_text(html)

        assert "Matt MacInnis is the CPO at Rippling." in result
        assert "We discuss:" in result
        assert "1. Why extraordinary results" in result
        assert "Google Gemini (https://ai.dev/)" in result

    def test_url_encoded_link(self):
        """Test that URL-encoded links are decoded."""
        html = '<a href="https://example.com/path%20with%20spaces">Link</a>'
        result = html_to_plain_text(html)
        assert "https://example.com/path with spaces" in result

    def test_div_tags_removed(self):
        """Test that div tags are removed but content preserved."""
        html = "<div>Content in div</div>"
        result = html_to_plain_text(html)
        assert result == "Content in div"

    def test_span_tags_removed(self):
        """Test that span tags are removed but content preserved."""
        html = '<span class="highlight">Highlighted text</span>'
        result = html_to_plain_text(html)
        assert result == "Highlighted text"


class TestExtractLinksFromHtml:
    """Tests for extract_links_from_html function."""

    def test_none_input(self):
        """Test that None input returns empty list."""
        assert extract_links_from_html(None) == []

    def test_empty_string(self):
        """Test that empty string returns empty list."""
        assert extract_links_from_html("") == []

    def test_no_links(self):
        """Test text without links returns empty list."""
        html = "<p>No links here</p>"
        assert extract_links_from_html(html) == []

    def test_single_link(self):
        """Test extracting a single link."""
        html = '<a href="https://example.com">Example</a>'
        result = extract_links_from_html(html)
        assert len(result) == 1
        assert result[0]["text"] == "Example"
        assert result[0]["url"] == "https://example.com"

    def test_multiple_links(self):
        """Test extracting multiple links."""
        html = """
        <a href="https://first.com">First</a>
        <a href="https://second.com">Second</a>
        """
        result = extract_links_from_html(html)
        assert len(result) == 2
        assert result[0]["url"] == "https://first.com"
        assert result[1]["url"] == "https://second.com"

    def test_link_with_nested_tags(self):
        """Test link with nested formatting tags."""
        html = '<a href="https://example.com"><strong>Bold</strong> text</a>'
        result = extract_links_from_html(html)
        assert len(result) == 1
        assert result[0]["text"] == "Bold text"

    def test_javascript_links_skipped(self):
        """Test that javascript: links are skipped."""
        html = '<a href="javascript:void(0)">Click</a>'
        result = extract_links_from_html(html)
        assert len(result) == 0

    def test_anchor_links_skipped(self):
        """Test that # anchor links are skipped."""
        html = '<a href="#section">Jump</a>'
        result = extract_links_from_html(html)
        assert len(result) == 0

    def test_url_decoded(self):
        """Test that URL-encoded links are decoded."""
        html = '<a href="https://example.com/path%20with%20spaces">Link</a>'
        result = extract_links_from_html(html)
        assert result[0]["url"] == "https://example.com/path with spaces"

    def test_real_world_description(self):
        """Test extracting links from real RSS description."""
        html = """
        <p><strong>Transcript:</strong> <a href="https://newsletter.com/episode-123">https://newsletter.com/episode-123</a></p>
        <p>• X: <a href="https://x.com/guest">https://x.com/guest</a></p>
        <p>• LinkedIn: <a href="https://linkedin.com/in/guest">https://linkedin.com/in/guest</a></p>
        """
        result = extract_links_from_html(html)
        assert len(result) == 3
        urls = [link["url"] for link in result]
        assert "https://newsletter.com/episode-123" in urls
        assert "https://x.com/guest" in urls
        assert "https://linkedin.com/in/guest" in urls
