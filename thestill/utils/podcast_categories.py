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

"""
Apple Podcasts category taxonomy and validation.

This module provides validation for podcast categories against the official
Apple Podcasts taxonomy (19 main categories + ~80 subcategories).

Reference: https://podcasters.apple.com/support/1691-apple-podcasts-categories
"""

from dataclasses import dataclass
from typing import Optional

# Apple Podcasts taxonomy: main categories mapped to their valid subcategories
# Categories without subcategories map to empty sets
APPLE_PODCAST_TAXONOMY: dict[str, set[str]] = {
    "Arts": {
        "Books",
        "Design",
        "Fashion & Beauty",
        "Food",
        "Performing Arts",
        "Visual Arts",
    },
    "Business": {
        "Careers",
        "Entrepreneurship",
        "Investing",
        "Management",
        "Marketing",
        "Non-Profit",
    },
    "Comedy": {
        "Comedy Interviews",
        "Improv",
        "Stand-Up",
    },
    "Education": {
        "Courses",
        "How To",
        "Language Learning",
        "Self-Improvement",
    },
    "Fiction": {
        "Comedy Fiction",
        "Drama",
        "Science Fiction",
    },
    "Government": set(),  # No subcategories
    "Health & Fitness": {
        "Alternative Health",
        "Fitness",
        "Medicine",
        "Mental Health",
        "Nutrition",
        "Sexuality",
    },
    "History": set(),  # No subcategories
    "Kids & Family": {
        "Education for Kids",
        "Parenting",
        "Pets & Animals",
        "Stories for Kids",
    },
    "Leisure": {
        "Animation & Manga",
        "Automotive",
        "Aviation",
        "Crafts",
        "Games",
        "Hobbies",
        "Home & Garden",
        "Video Games",
    },
    "Music": {
        "Music Commentary",
        "Music History",
        "Music Interviews",
    },
    "News": {
        "Business News",
        "Daily News",
        "Entertainment News",
        "News Commentary",
        "Politics",
        "Sports News",
        "Tech News",
    },
    "Religion & Spirituality": {
        "Buddhism",
        "Christianity",
        "Hinduism",
        "Islam",
        "Judaism",
        "Religion",
        "Spirituality",
    },
    "Science": {
        "Astronomy",
        "Chemistry",
        "Earth Sciences",
        "Life Sciences",
        "Mathematics",
        "Natural Sciences",
        "Nature",
        "Physics",
        "Social Sciences",
    },
    "Society & Culture": {
        "Documentary",
        "Personal Journals",
        "Philosophy",
        "Places & Travel",
        "Relationships",
    },
    "Sports": {
        "Baseball",
        "Basketball",
        "Cricket",
        "Fantasy Sports",
        "Football",
        "Golf",
        "Hockey",
        "Rugby",
        "Running",
        "Soccer",
        "Swimming",
        "Tennis",
        "Volleyball",
        "Wilderness",
        "Wrestling",
    },
    "Technology": set(),  # No subcategories
    "True Crime": set(),  # No subcategories
    "TV & Film": {
        "After Shows",
        "Film History",
        "Film Interviews",
        "Film Reviews",
        "TV Reviews",
    },
}

# Set of all valid main categories for quick lookup
VALID_CATEGORIES: set[str] = set(APPLE_PODCAST_TAXONOMY.keys())


@dataclass
class ValidatedCategory:
    """Result of category validation."""

    category: Optional[str]
    subcategory: Optional[str]


def validate_category(category: Optional[str], subcategory: Optional[str] = None) -> ValidatedCategory:
    """
    Validate a podcast category and subcategory against Apple's taxonomy.

    Args:
        category: Main category (e.g., "Society & Culture")
        subcategory: Subcategory (e.g., "Documentary")

    Returns:
        ValidatedCategory with validated values:
        - If category is invalid: (None, None)
        - If category is valid but subcategory is invalid: (category, None)
        - If both are valid: (category, subcategory)
    """
    if not category:
        return ValidatedCategory(category=None, subcategory=None)

    # Check if main category is valid
    if category not in VALID_CATEGORIES:
        return ValidatedCategory(category=None, subcategory=None)

    # If no subcategory provided, return just the category
    if not subcategory:
        return ValidatedCategory(category=category, subcategory=None)

    # Check if subcategory is valid for this category
    valid_subcategories = APPLE_PODCAST_TAXONOMY.get(category, set())
    if subcategory not in valid_subcategories:
        # Invalid subcategory - return category only
        return ValidatedCategory(category=category, subcategory=None)

    return ValidatedCategory(category=category, subcategory=subcategory)


def is_valid_category(category: str) -> bool:
    """Check if a category name is valid."""
    return category in VALID_CATEGORIES


def is_valid_subcategory(category: str, subcategory: str) -> bool:
    """Check if a subcategory is valid for the given category."""
    if category not in VALID_CATEGORIES:
        return False
    valid_subcategories = APPLE_PODCAST_TAXONOMY.get(category, set())
    return subcategory in valid_subcategories


def get_subcategories(category: str) -> set[str]:
    """Get all valid subcategories for a given category."""
    return APPLE_PODCAST_TAXONOMY.get(category, set())
