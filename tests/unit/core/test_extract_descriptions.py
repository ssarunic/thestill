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

"""Tests for RSSMediaSource._extract_descriptions field-inversion handling.

The Guardian's Today in Focus feed puts real HTML in <description> and
double-escaped tag remnants in <itunes:summary> (surfaced via entry.content)
— the inverse of the historical assumption that description is plain and
content is HTML. Extraction must pick by content, not field position.
"""

import feedparser

from thestill.core.media_source import RSSMediaSource

GUARDIAN_STYLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>Swimming pools in Gaza</title>
      <description>Journalist &lt;strong&gt;Amjed Tantesh&lt;/strong&gt; talks about swimming. Support us at &lt;a href=&quot;https://www.theguardian.com/infocus&quot;&gt;theguardian.com/infocus&lt;/a&gt;</description>
      <itunes:summary>Journalist Amjed Tantesh talks about swimming. Support us at &amp;lt;a href=&amp;quot;https://www.theguardian.com/infocus&amp;quot;&amp;gt;theguardian.com/infocus&amp;lt;/a&amp;gt;</itunes:summary>
    </item>
  </channel>
</rss>"""

CONVENTIONAL_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>Plain description, HTML content</title>
      <description>Episode about databases.</description>
      <content:encoded><![CDATA[<p>Episode about <strong>databases</strong>. See <a href="https://example.com/notes">notes</a>.</p>]]></content:encoded>
    </item>
  </channel>
</rss>"""


def _extract(feed_xml: str) -> tuple[str, str]:
    entry = feedparser.parse(feed_xml).entries[0]
    return RSSMediaSource()._extract_descriptions(entry)


def test_guardian_style_feed_html_comes_from_description():
    plain, html = _extract(GUARDIAN_STYLE_FEED)

    assert '<a href="https://www.theguardian.com/infocus">' in html
    assert "<strong>" in html
    # The plain variant is derived from the HTML one: no tags, no escaped remnants.
    assert "<" not in plain and "&lt;" not in plain
    assert "theguardian.com/infocus" in plain


def test_conventional_feed_keeps_content_as_html():
    plain, html = _extract(CONVENTIONAL_FEED)

    assert html.startswith("<p>")
    assert '<a href="https://example.com/notes">' in html
    assert "<" not in plain
    assert "notes (https://example.com/notes)" in plain
