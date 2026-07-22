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

"""HTML utility functions for converting RSS feed descriptions to plain text."""

import html as html_module
import re
from typing import Optional, Sequence

# Tags that podcast feeds legitimately use in descriptions (mirrors the
# frontend sanitizer allowlist, plus structural tags html_to_plain_text
# understands). Recognition is restricted to these names so prose like
# "i<n and j>k" is never mistaken for markup.
_KNOWN_TAG_RE = re.compile(
    r"</?(?:p|br|a|strong|b|em|i|u|ul|ol|li|div|span|h[1-6]|hr|img|blockquote)\b[^<>]*/?>",
    re.IGNORECASE,
)


def html_to_plain_text(html_text: Optional[str]) -> str:
    """
    Convert HTML to readable plain text while preserving structure.

    This function is designed for RSS feed descriptions which commonly contain:
    - Paragraph tags (<p>) for structure
    - Line breaks (<br>)
    - Links (<a href>) - preserved as "text (url)" format
    - Bold/strong text (<strong>, <b>)
    - Italic/emphasis (<em>, <i>)
    - Lists (<ul>, <ol>, <li>)
    - Horizontal rules (<hr>)

    Args:
        html_text: HTML string to convert, or None

    Returns:
        Plain text with preserved structure and links in parentheses format

    Example:
        >>> html = '<p><strong>Guest:</strong> John Smith</p><p>Link: <a href="https://example.com">click here</a></p>'
        >>> html_to_plain_text(html)
        'Guest: John Smith\\n\\nLink: click here (https://example.com)'
    """
    if not html_text:
        return ""

    text = html_text

    # Handle paragraph breaks - convert closing/opening p tags to double newline
    text = re.sub(r"</p>\s*<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>|</p>", "", text, flags=re.IGNORECASE)

    # Handle line breaks
    text = re.sub(r"<br\s*/?>|<br>", "\n", text, flags=re.IGNORECASE)

    # Handle lists - convert list items to bullet points
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?[uo]l[^>]*>", "", text, flags=re.IGNORECASE)

    # Handle links - preserve URL in parentheses
    def replace_link(match: re.Match) -> str:
        href = match.group(1)
        link_content = match.group(2)
        # Remove any nested tags from link text
        link_text = re.sub(r"<[^>]+>", "", link_content).strip()
        # Remove invisible Unicode characters (word joiners, etc.)
        link_text_clean = link_text.strip("\u2060\u200b\u200c\u200d\ufeff")

        # Skip invalid or javascript URLs
        if not href or href.startswith(("%", "javascript:")):
            return link_text

        # URL-decode the href if it's percent-encoded
        try:
            from urllib.parse import unquote

            href_decoded = unquote(href)
        except Exception:
            href_decoded = href

        # Avoid duplication if link text is already the URL
        if link_text_clean == href or link_text_clean == href_decoded:
            return href_decoded

        # Return "text (url)" format
        return f"{link_text} ({href_decoded})"

    text = re.sub(
        r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
        replace_link,
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Remove styling tags but keep their content
    text = re.sub(r"</?(?:strong|b|em|i|span|div)[^>]*>", "", text, flags=re.IGNORECASE)

    # Handle horizontal rules
    text = re.sub(r"<hr\s*/?>", "---", text, flags=re.IGNORECASE)

    # Handle headings - add newlines around them
    text = re.sub(r"<h[1-6][^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)

    # Remove any remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities (&amp; -> &, &lt; -> <, etc.)
    text = html_module.unescape(text)

    # Clean up whitespace
    # - Collapse multiple newlines to max 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # - Collapse multiple spaces/tabs to single space
    text = re.sub(r"[ \t]+", " ", text)
    # - Remove leading/trailing whitespace from each line
    text = "\n".join(line.strip() for line in text.split("\n"))
    # - Remove leading/trailing whitespace from entire text
    text = text.strip()

    return text


def count_html_tags(text: str) -> int:
    """Count real (unescaped) HTML tags from the known-tag set in ``text``."""
    return len(_KNOWN_TAG_RE.findall(text))


def unescape_entities_stable(text: str, max_passes: int = 3) -> str:
    """Unescape HTML entities repeatedly until the text stops changing.

    Some feeds double-escape markup (e.g. The Guardian's ``itunes:summary``
    carries ``&amp;lt;a href=...``, which is still ``&lt;a href=...`` after
    feedparser's single XML decode). Bounded so a pathological input can't
    loop forever.
    """
    for _ in range(max_passes):
        unescaped = html_module.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return text


def resolve_description_variants(candidates: Sequence[str]) -> tuple[str, str]:
    """Pick the plain-text and HTML description from feed-provided variants.

    RSS feeds offer the description in several places (``description``,
    ``content:encoded``, ``itunes:summary``) with no reliable convention for
    which one carries markup — The Guardian, for instance, puts real HTML in
    ``description`` and escaped tag remnants in ``itunes:summary``. So instead
    of trusting field position, inspect the content: the candidate with the
    most real markup becomes the HTML variant, and the plain variant is
    derived from it, making the pair consistent by construction.

    Args:
        candidates: Description variants in preference order (ties in markup
            richness go to the earliest candidate).

    Returns:
        Tuple of (plain_text_description, html_description). The HTML slot is
        ``""`` when no candidate contains markup, even after unescaping.
    """
    non_empty = [c.strip() for c in candidates if c and c.strip()]
    if not non_empty:
        return "", ""

    best = max(non_empty, key=count_html_tags)
    if count_html_tags(best) == 0:
        # No real markup anywhere — but double-escaped feeds hide theirs
        # behind entities, so check whether unescaping reveals tags.
        revealed = max((unescape_entities_stable(c) for c in non_empty), key=count_html_tags)
        if count_html_tags(revealed) == 0:
            # Genuinely plain-text feed. Keep the richest variant — some
            # feeds pair a short teaser with fuller plain-text notes, and
            # dropping everything but the first candidate would lose them.
            return max(non_empty, key=len), ""
        best = revealed

    return html_to_plain_text(best), best


def extract_links_from_html(html_text: Optional[str]) -> list[dict[str, str]]:
    """
    Extract all links from HTML text.

    Args:
        html_text: HTML string to parse, or None

    Returns:
        List of dicts with 'text' and 'url' keys

    Example:
        >>> html = '<a href="https://example.com">Example</a> and <a href="https://test.com">Test</a>'
        >>> extract_links_from_html(html)
        [{'text': 'Example', 'url': 'https://example.com'}, {'text': 'Test', 'url': 'https://test.com'}]
    """
    if not html_text:
        return []

    links = []
    pattern = r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>'

    for match in re.finditer(pattern, html_text, flags=re.DOTALL | re.IGNORECASE):
        href = match.group(1)
        link_text = re.sub(r"<[^>]+>", "", match.group(2)).strip()

        # Skip invalid URLs
        if not href or href.startswith(("%", "javascript:", "#")):
            continue

        # URL-decode if needed
        try:
            from urllib.parse import unquote

            href = unquote(href)
        except Exception:
            pass

        links.append({"text": link_text, "url": href})

    return links
