"""Tests for the JSON-backed Apple Podcasts taxonomy loader."""

from thestill.utils.podcast_categories import (
    APPLE_GENRE_IDS,
    APPLE_PODCAST_TAXONOMY,
    VALID_CATEGORIES,
    get_subcategories,
    is_valid_category,
    is_valid_subcategory,
    normalize_category_name,
    validate_category,
)


class TestTaxonomyLoading:
    def test_taxonomy_has_19_top_level_categories(self):
        """Apple defines 19 top-level podcast categories."""
        assert len(VALID_CATEGORIES) == 19
        assert len(APPLE_PODCAST_TAXONOMY) == 19

    def test_well_known_categories_present(self):
        for cat in ["Arts", "Business", "Comedy", "News", "True Crime", "Technology"]:
            assert cat in VALID_CATEGORIES

    def test_government_history_technology_truecrime_have_no_subcategories(self):
        """These four top-levels are leaf nodes per Apple's taxonomy."""
        for cat in ["Government", "History", "Technology", "True Crime"]:
            assert APPLE_PODCAST_TAXONOMY[cat] == set()

    def test_news_subcategories_complete(self):
        assert APPLE_PODCAST_TAXONOMY["News"] == {
            "Business News",
            "Daily News",
            "Entertainment News",
            "News Commentary",
            "Politics",
            "Sports News",
            "Tech News",
        }

    def test_sports_uses_apple_official_spelling(self):
        """Sports subcategories must match Apple's official names exactly.

        Why: the previous in-code taxonomy used 'Football' and 'Soccer', which
        don't match Apple's 'American Football' and 'Football (Soccer)'. Any
        regression to the old names breaks RSS validation against real feeds.
        """
        sports = APPLE_PODCAST_TAXONOMY["Sports"]
        assert "American Football" in sports
        assert "Football (Soccer)" in sports
        assert "Football" not in sports
        assert "Soccer" not in sports

    def test_genre_ids_loaded_for_all_top_levels(self):
        for cat in VALID_CATEGORIES:
            assert APPLE_GENRE_IDS[cat] > 0

    def test_known_genre_ids(self):
        assert APPLE_GENRE_IDS["News"] == 1489
        assert APPLE_GENRE_IDS["True Crime"] == 1488
        assert APPLE_GENRE_IDS["Comedy"] == 1303


class TestValidateCategory:
    def test_valid_category_and_subcategory(self):
        result = validate_category("News", "Politics")
        assert result.category == "News"
        assert result.subcategory == "Politics"

    def test_valid_category_only(self):
        result = validate_category("News")
        assert result.category == "News"
        assert result.subcategory is None

    def test_invalid_subcategory_falls_back_to_category(self):
        """Bad subcategory should not invalidate a good category."""
        result = validate_category("News", "NotARealSubcategory")
        assert result.category == "News"
        assert result.subcategory is None

    def test_invalid_category_returns_both_none(self):
        result = validate_category("MadeUpCategory", "Politics")
        assert result.category is None
        assert result.subcategory is None

    def test_empty_inputs_return_none(self):
        result = validate_category(None, None)
        assert result.category is None
        assert result.subcategory is None
        result = validate_category("", "Politics")
        assert result.category is None


class TestHelperLookups:
    def test_is_valid_category(self):
        assert is_valid_category("News")
        assert not is_valid_category("Bogus")

    def test_is_valid_subcategory(self):
        assert is_valid_subcategory("News", "Politics")
        assert not is_valid_subcategory("News", "MadeUp")
        assert not is_valid_subcategory("Bogus", "Politics")

    def test_get_subcategories(self):
        assert "Politics" in get_subcategories("News")
        # Top-level with no children returns empty set, not None.
        assert get_subcategories("Technology") == set()
        # Unknown category returns empty set rather than raising.
        assert get_subcategories("Bogus") == set()


class TestNormalizeCategoryName:
    def test_strips_whitespace_and_punctuation_and_lowercases(self):
        # Same canonical form for all common variants.
        canonical = normalize_category_name("American Football")
        assert normalize_category_name("  american  football  ") == canonical
        assert normalize_category_name("American-Football") == canonical
        assert normalize_category_name("AMERICAN_FOOTBALL") == canonical

    def test_empty_and_none_return_empty_string(self):
        assert normalize_category_name(None) == ""
        assert normalize_category_name("") == ""
        assert normalize_category_name("   ") == ""
