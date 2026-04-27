"""Tests for the categories table, FK schema, migration, and CRUD round-trip
in SqlitePodcastRepository.
"""

import sqlite3
import uuid

import pytest

from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def temp_db(tmp_path):
    """Fresh repo backed by a tmp-file SQLite DB."""
    db_path = tmp_path / "test.db"
    return SqlitePodcastRepository(str(db_path))


def _new_podcast(rss_url: str, title: str = "T", **fields) -> Podcast:
    return Podcast(
        id=str(uuid.uuid4()),
        rss_url=rss_url,
        title=title,
        description="d",
        episodes=[],
        **fields,
    )


# ---------------------------------------------------------------------------
# Schema and seeding
# ---------------------------------------------------------------------------


class TestSchemaAndSeed:
    def test_categories_table_exists(self, temp_db):
        with temp_db._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categories'")
            assert cursor.fetchone() is not None

    def test_seed_creates_19_top_level_and_subcategories(self, temp_db):
        with temp_db._get_connection() as conn:
            top = conn.execute("SELECT COUNT(*) AS n FROM categories WHERE parent_id IS NULL").fetchone()
            sub = conn.execute("SELECT COUNT(*) AS n FROM categories WHERE parent_id IS NOT NULL").fetchone()
        assert top["n"] == 19
        # Apple has ~80 subcategories; the exact count is taxonomy-dependent.
        # Asserting >= 80 catches accidental drops without being brittle to additions.
        assert sub["n"] >= 80

    def test_seed_includes_apple_genre_ids_for_top_level_only(self, temp_db):
        with temp_db._get_connection() as conn:
            top_with_id = conn.execute(
                "SELECT COUNT(*) AS n FROM categories WHERE parent_id IS NULL AND apple_genre_id IS NOT NULL"
            ).fetchone()
            sub_with_id = conn.execute(
                "SELECT COUNT(*) AS n FROM categories WHERE parent_id IS NOT NULL AND apple_genre_id IS NOT NULL"
            ).fetchone()
        assert top_with_id["n"] == 19  # every top-level has a genre_id
        assert sub_with_id["n"] == 0  # subcategories never do

    def test_podcasts_has_fk_columns_not_legacy_text(self, temp_db):
        with temp_db._get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(podcasts)").fetchall()}
        assert "primary_category_id" in cols
        assert "secondary_category_id" in cols
        assert "primary_category" not in cols
        assert "primary_subcategory" not in cols
        assert "secondary_category" not in cols
        assert "secondary_subcategory" not in cols

    def test_unique_constraint_on_name_and_parent(self, temp_db):
        """Same category name under different parents is allowed; same name
        twice under the same parent (or twice at top-level) is rejected."""
        with temp_db._get_connection() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO categories (name, slug, parent_id, apple_genre_id) "
                    "VALUES ('News', 'news', NULL, 1489)"
                )

    def test_init_is_idempotent(self, tmp_path):
        """Re-opening the same DB must not re-seed or duplicate categories."""
        db_path = tmp_path / "t.db"
        SqlitePodcastRepository(str(db_path))
        repo2 = SqlitePodcastRepository(str(db_path))
        with repo2._get_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
        # Same count after second init = no duplicate seed.
        assert n == len(repo2._cat_id_to_pair)


# ---------------------------------------------------------------------------
# Resolver helpers (string ↔ FK)
# ---------------------------------------------------------------------------


class TestResolver:
    def test_resolve_top_and_sub_returns_subcategory_id(self, temp_db):
        cat_id = temp_db._resolve_category_strings_to_id("News", "Politics")
        assert cat_id is not None
        top, sub = temp_db._resolve_category_id_to_pair(cat_id)
        assert (top, sub) == ("News", "Politics")

    def test_resolve_top_only(self, temp_db):
        cat_id = temp_db._resolve_category_strings_to_id("True Crime", None)
        assert cat_id is not None
        assert temp_db._resolve_category_id_to_pair(cat_id) == ("True Crime", None)

    def test_resolve_unknown_subcategory_falls_back_to_top(self, temp_db):
        """Best-effort matching (Q4-iii): bad sub keeps the top-level FK."""
        good = temp_db._resolve_category_strings_to_id("News", None)
        fallback = temp_db._resolve_category_strings_to_id("News", "NotASubcategory")
        assert fallback == good

    def test_resolve_unknown_top_returns_none(self, temp_db):
        assert temp_db._resolve_category_strings_to_id("AlienGenre", "X") is None

    def test_resolve_handles_case_and_whitespace(self, temp_db):
        """Tolerant matching for slightly-off RSS strings."""
        canonical = temp_db._resolve_category_strings_to_id("News", "Politics")
        assert temp_db._resolve_category_strings_to_id("  news  ", "POLITICS") == canonical

    def test_resolve_none_inputs(self, temp_db):
        assert temp_db._resolve_category_strings_to_id(None, None) is None
        assert temp_db._resolve_category_strings_to_id(None, "Politics") is None

    def test_resolve_id_to_pair_unknown_id(self, temp_db):
        assert temp_db._resolve_category_id_to_pair(99999999) == (None, None)
        assert temp_db._resolve_category_id_to_pair(None) == (None, None)


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadRoundTrip:
    def test_save_and_load_full_categories(self, temp_db):
        p = _new_podcast(
            "https://e.x/a.xml",
            "A",
            primary_category="News",
            primary_subcategory="Politics",
            secondary_category="Comedy",
            secondary_subcategory="Stand-Up",
        )
        temp_db.save_podcast(p)
        got = temp_db.get_by_url("https://e.x/a.xml")
        assert got.primary_category == "News"
        assert got.primary_subcategory == "Politics"
        assert got.secondary_category == "Comedy"
        assert got.secondary_subcategory == "Stand-Up"

    def test_save_with_unknown_subcategory_falls_back_to_category_only(self, temp_db):
        p = _new_podcast(
            "https://e.x/b.xml",
            "B",
            primary_category="News",
            primary_subcategory="MadeUpSub",
        )
        temp_db.save_podcast(p)
        got = temp_db.get_by_url("https://e.x/b.xml")
        assert got.primary_category == "News"
        assert got.primary_subcategory is None

    def test_save_with_unknown_category_clears_both(self, temp_db):
        p = _new_podcast(
            "https://e.x/c.xml",
            "C",
            primary_category="AlienGenre",
            primary_subcategory="Whatever",
        )
        temp_db.save_podcast(p)
        got = temp_db.get_by_url("https://e.x/c.xml")
        assert got.primary_category is None
        assert got.primary_subcategory is None

    def test_save_with_no_categories(self, temp_db):
        p = _new_podcast("https://e.x/d.xml", "D")
        temp_db.save_podcast(p)
        got = temp_db.get_by_url("https://e.x/d.xml")
        assert got.primary_category is None
        assert got.secondary_category is None

    def test_save_via_save_method_persists_categories(self, temp_db):
        """save() (the destructive UPSERT path) also resolves FKs correctly."""
        p = _new_podcast(
            "https://e.x/e.xml",
            "E",
            primary_category="Technology",
            primary_subcategory=None,
        )
        temp_db.save(p)
        got = temp_db.get_by_url("https://e.x/e.xml")
        assert got.primary_category == "Technology"

    def test_save_podcast_idempotent_when_categories_unchanged(self, temp_db):
        """save_podcast should detect 'no change' even when categories are FK-backed."""
        p = _new_podcast(
            "https://e.x/f.xml",
            "F",
            primary_category="News",
            primary_subcategory="Politics",
        )
        temp_db.save_podcast(p)
        first = temp_db.get_by_url("https://e.x/f.xml")
        # Sleep-free idempotency check: re-save and compare updated_at.
        temp_db.save_podcast(p)
        second = temp_db.get_by_url("https://e.x/f.xml")
        assert first.last_processed == second.last_processed

    def test_save_podcast_detects_category_change(self, temp_db):
        """Changing only the category should be detected as a real change."""
        p = _new_podcast(
            "https://e.x/g.xml",
            "G",
            primary_category="News",
            primary_subcategory="Politics",
        )
        temp_db.save_podcast(p)
        # Change primary subcategory
        p.primary_subcategory = "Daily News"
        temp_db.save_podcast(p)
        got = temp_db.get_by_url("https://e.x/g.xml")
        assert got.primary_subcategory == "Daily News"

    def test_fk_column_value_is_subcategory_id_when_sub_present(self, temp_db):
        p = _new_podcast(
            "https://e.x/h.xml",
            "H",
            primary_category="News",
            primary_subcategory="Politics",
        )
        temp_db.save_podcast(p)
        with temp_db._get_connection() as conn:
            row = conn.execute(
                "SELECT primary_category_id, secondary_category_id FROM podcasts WHERE rss_url = ?",
                ("https://e.x/h.xml",),
            ).fetchone()
            cat_row = conn.execute(
                "SELECT name, parent_id FROM categories WHERE id = ?", (row["primary_category_id"],)
            ).fetchone()
        assert cat_row["name"] == "Politics"
        assert cat_row["parent_id"] is not None  # it's a subcategory


# ---------------------------------------------------------------------------
# Migration from legacy free-text schema
# ---------------------------------------------------------------------------


class TestMigrationFromLegacy:
    """Verify the one-time migration: legacy DB with 4 free-text category
    columns → normalized FK schema, with best-effort backfill."""

    def _build_legacy_db(self, db_path) -> None:
        raw = sqlite3.connect(str(db_path))
        raw.executescript(
            """
            CREATE TABLE podcasts (
                id TEXT PRIMARY KEY NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                rss_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                primary_category TEXT NULL,
                primary_subcategory TEXT NULL,
                secondary_category TEXT NULL,
                secondary_subcategory TEXT NULL,
                CHECK (length(id) = 36),
                CHECK (length(rss_url) > 0)
            );
            CREATE INDEX idx_podcasts_primary_category ON podcasts(primary_category);
            CREATE INDEX idx_podcasts_secondary_category ON podcasts(secondary_category);
            """
        )
        raw.executemany(
            """INSERT INTO podcasts (id, slug, rss_url, title,
                primary_category, primary_subcategory, secondary_category, secondary_subcategory)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("00000000-0000-0000-0000-000000000001", "a", "https://e/a", "A", "News", "Politics", None, None),
                (
                    "00000000-0000-0000-0000-000000000002",
                    "b",
                    "https://e/b",
                    "B",
                    "true crime",
                    None,
                    "Comedy",
                    "Stand-Up",
                ),  # tolerant casing
                (
                    "00000000-0000-0000-0000-000000000003",
                    "c",
                    "https://e/c",
                    "C",
                    "Made-Up Cat",
                    "X",
                    None,
                    None,
                ),  # unresolved → NULL
                (
                    "00000000-0000-0000-0000-000000000004",
                    "d",
                    "https://e/d",
                    "D",
                    None,
                    None,
                    None,
                    None,
                ),  # all-null preserved
            ],
        )
        raw.commit()
        raw.close()

    def test_migration_creates_categories_table_and_drops_legacy_columns(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        self._build_legacy_db(db_path)
        SqlitePodcastRepository(str(db_path))
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(podcasts)").fetchall()}
            assert "primary_category" not in cols
            assert "primary_category_id" in cols
            tables = {
                row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            assert "categories" in tables

    def test_migration_backfills_known_categories(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        self._build_legacy_db(db_path)
        repo = SqlitePodcastRepository(str(db_path))
        with repo._get_connection() as conn:
            rows = {
                row["id"]: row
                for row in conn.execute(
                    "SELECT id, primary_category_id, secondary_category_id FROM podcasts"
                ).fetchall()
            }

        # Row A: News + Politics → both top and sub matched, FK = Politics id.
        a = rows["00000000-0000-0000-0000-000000000001"]
        assert repo._resolve_category_id_to_pair(a["primary_category_id"]) == ("News", "Politics")
        assert a["secondary_category_id"] is None

        # Row B: tolerant casing 'true crime' resolves to True Crime.
        b = rows["00000000-0000-0000-0000-000000000002"]
        assert repo._resolve_category_id_to_pair(b["primary_category_id"]) == ("True Crime", None)
        assert repo._resolve_category_id_to_pair(b["secondary_category_id"]) == ("Comedy", "Stand-Up")

        # Row C: unresolvable → NULL (lossy is OK per Q4-iii).
        c = rows["00000000-0000-0000-0000-000000000003"]
        assert c["primary_category_id"] is None
        assert c["secondary_category_id"] is None

        # Row D: all-null preserved.
        d = rows["00000000-0000-0000-0000-000000000004"]
        assert d["primary_category_id"] is None

    def test_migration_is_idempotent_on_already_migrated_db(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        self._build_legacy_db(db_path)
        repo1 = SqlitePodcastRepository(str(db_path))
        # Re-open: should not fail and should not duplicate categories.
        repo2 = SqlitePodcastRepository(str(db_path))
        with repo2._get_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
        assert n == len(repo1._cat_id_to_pair) == len(repo2._cat_id_to_pair)

    def test_migration_recovers_from_interrupted_backfill(self, tmp_path):
        """Simulate the worst case: a previous migration run added the FK
        columns but crashed before backfilling. Without crash-recovery, the
        next run would skip backfill (FK columns already exist) and then
        drop the legacy columns — silent permanent data loss.
        """
        db_path = tmp_path / "interrupted.db"
        self._build_legacy_db(db_path)
        # Hand-simulate the post-crash state: categories table seeded, FK
        # columns present but NULL, legacy free-text columns still present.
        raw = sqlite3.connect(str(db_path))
        raw.executescript(
            """
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                parent_id INTEGER NULL,
                apple_genre_id INTEGER NULL,
                FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE CASCADE
            );
            -- Manually add the FK columns but leave them NULL (the crash point).
            ALTER TABLE podcasts ADD COLUMN primary_category_id INTEGER NULL
                REFERENCES categories(id) ON DELETE SET NULL;
            ALTER TABLE podcasts ADD COLUMN secondary_category_id INTEGER NULL
                REFERENCES categories(id) ON DELETE SET NULL;
            """
        )
        raw.commit()
        raw.close()

        # Re-open with the repo: it should detect the half-migrated state and
        # re-run the backfill from the still-present legacy columns before
        # dropping them.
        repo = SqlitePodcastRepository(str(db_path))
        with repo._get_connection() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(podcasts)").fetchall()}
            assert "primary_category" not in cols  # legacy dropped
            assert "primary_category_id" in cols  # FK present
            row = conn.execute(
                "SELECT primary_category_id FROM podcasts " "WHERE id = '00000000-0000-0000-0000-000000000001'"
            ).fetchone()
        # Recovered: the FK is non-NULL and resolves back to the original (News, Politics).
        assert row["primary_category_id"] is not None
        assert repo._resolve_category_id_to_pair(row["primary_category_id"]) == (
            "News",
            "Politics",
        )
