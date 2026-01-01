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

"""HTML utility functions for converting RSS feed descriptions to plain text."""

import html as html_module
import re
from typing import Optional


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
