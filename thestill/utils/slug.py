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

"""
Slug generation utilities for URL/filesystem-safe identifiers.

Uses python-slugify for proper Unicode transliteration and edge case handling.
"""


from slugify import slugify as python_slugify

# Maximum length for generated slugs (leaves room for file extensions and suffixes)
MAX_SLUG_LENGTH = 100


def generate_slug(text: str, max_length: int = MAX_SLUG_LENGTH) -> str:
    """
    Generate a URL/filesystem-safe slug from text.

    Args:
        text: Text to convert to slug (e.g., podcast or episode title)
        max_length: Maximum length of the generated slug

    Returns:
        Lowercase string with spaces/special chars replaced by hyphens.
        Returns "unnamed" if the text produces an empty slug.

    Examples:
        >>> generate_slug("Prof G Markets")
        'prof-g-markets'
        >>> generate_slug("Café & Croissants!")
        'cafe-and-croissants'
        >>> generate_slug("The Daily Show: Episode #123")
        'the-daily-show-episode-123'
    """
    slug = python_slugify(text, max_length=max_length)
    return slug or "unnamed"
