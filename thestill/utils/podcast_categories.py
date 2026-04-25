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
Apple Podcasts category taxonomy and validation.

The canonical taxonomy lives in ``data/podcast_categories.json`` (Apple's
official 19 main categories + ~80 subcategories, plus the ``genre_id`` Apple
uses on its iTunes chart APIs). This module loads that JSON at import time and
exposes the same validation helpers the codebase has always used, so call
sites do not need to change.

Reference: https://podcasters.apple.com/support/1691-apple-podcasts-categories
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Resolve the JSON file relative to the repo root (this module lives in
# thestill/utils/, so the data dir is two parents up + /data/).
_TAXONOMY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "podcast_categories.json"


def _load_taxonomy_and_genre_ids() -> tuple[dict[str, set[str]], dict[str, int]]:
    """Load the Apple taxonomy and per-category genre_ids in a single read."""
    with _TAXONOMY_PATH.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    taxonomy: dict[str, set[str]] = {}
    genre_ids: dict[str, int] = {}
    for cat in payload["categories"]:
        taxonomy[cat["name"]] = set(cat.get("subcategories") or [])
        if cat.get("genre_id"):
            genre_ids[cat["name"]] = cat["genre_id"]
    return taxonomy, genre_ids


APPLE_PODCAST_TAXONOMY, APPLE_GENRE_IDS = _load_taxonomy_and_genre_ids()

# Set of all valid main categories for quick lookup.
VALID_CATEGORIES: set[str] = set(APPLE_PODCAST_TAXONOMY.keys())


def normalize_category_name(name: Optional[str]) -> str:
    """Normalize a category name for tolerant lookup.

    Lower-cases and strips everything except alphanumerics, so
    ``" American Football "`` and ``"american-football"`` map to the same
    key. Used by the repository for string→FK resolution where incoming RSS
    casing/whitespace varies from Apple's canonical spelling.
    """
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


@dataclass
class ValidatedCategory:
    """Result of category validation."""

    category: Optional[str]
    subcategory: Optional[str]


def validate_category(category: Optional[str], subcategory: Optional[str] = None) -> ValidatedCategory:
    """
    Validate a podcast category and subcategory against Apple's taxonomy.

    Returns:
        - (None, None) if category is invalid or empty
        - (category, None) if category is valid but subcategory is missing/invalid
        - (category, subcategory) if both are valid
    """
    if not category:
        return ValidatedCategory(category=None, subcategory=None)

    if category not in VALID_CATEGORIES:
        return ValidatedCategory(category=None, subcategory=None)

    if not subcategory:
        return ValidatedCategory(category=category, subcategory=None)

    valid_subcategories = APPLE_PODCAST_TAXONOMY.get(category, set())
    if subcategory not in valid_subcategories:
        return ValidatedCategory(category=category, subcategory=None)

    return ValidatedCategory(category=category, subcategory=subcategory)


def is_valid_category(category: str) -> bool:
    """Check if a category name is valid."""
    return category in VALID_CATEGORIES


def is_valid_subcategory(category: str, subcategory: str) -> bool:
    """Check if a subcategory is valid for the given category."""
    if category not in VALID_CATEGORIES:
        return False
    return subcategory in APPLE_PODCAST_TAXONOMY.get(category, set())


def get_subcategories(category: str) -> set[str]:
    """Get all valid subcategories for a given category."""
    return APPLE_PODCAST_TAXONOMY.get(category, set())
