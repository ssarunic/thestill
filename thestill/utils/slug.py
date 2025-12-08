# Copyright 2025 thestill.ai
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

from typing import Set

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


def generate_unique_slug(text: str, existing_slugs: Set[str], max_length: int = MAX_SLUG_LENGTH) -> str:
    """
    Generate a unique slug, appending -2, -3, etc. on collision.

    Args:
        text: Text to convert to slug
        existing_slugs: Set of slugs that already exist
        max_length: Maximum length of the generated slug

    Returns:
        A unique slug that is not in existing_slugs.

    Examples:
        >>> generate_unique_slug("My Podcast", set())
        'my-podcast'
        >>> generate_unique_slug("My Podcast", {"my-podcast"})
        'my-podcast-2'
        >>> generate_unique_slug("My Podcast", {"my-podcast", "my-podcast-2"})
        'my-podcast-3'
    """
    # Leave room for suffix like "-999" (5 chars)
    suffix_reserve = 5
    base = generate_slug(text, max_length=max_length - suffix_reserve)

    if base not in existing_slugs:
        return base

    counter = 2
    while f"{base}-{counter}" in existing_slugs:
        counter += 1

    return f"{base}-{counter}"
