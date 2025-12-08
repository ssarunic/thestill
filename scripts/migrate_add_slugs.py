#!/usr/bin/env python3
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
Migration script to add slug columns to existing SQLite database.

This script:
1. Backs up the database (optional but recommended)
2. Adds slug columns to podcasts and episodes tables if missing
3. Generates slugs for all existing records
4. Handles collisions by appending -2, -3, etc.

Usage:
    python scripts/migrate_add_slugs.py                    # Run migration
    python scripts/migrate_add_slugs.py --dry-run          # Preview changes
    python scripts/migrate_add_slugs.py --backup           # Backup before migration
    python scripts/migrate_add_slugs.py --db-path path.db  # Custom database path
"""

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Set

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from thestill.utils.slug import generate_unique_slug

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_default_db_path() -> Path:
    """Get default database path from config or fallback."""
    try:
        from thestill.utils.config import Config

        config = Config()
        return Path(config.database_path)
    except Exception:
        # Fallback to default location
        return Path("data/podcasts.db")


def backup_database(db_path: Path) -> Path:
    """Create a backup of the database."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.stem}_backup_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Database backed up to: {backup_path}")
    return backup_path


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def add_slug_columns(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """Add slug columns to podcasts and episodes tables if missing."""
    changes_needed = False

    # Check podcasts table
    if not column_exists(conn, "podcasts", "slug"):
        changes_needed = True
        if dry_run:
            logger.info("[DRY RUN] Would add 'slug' column to podcasts table")
        else:
            logger.info("Adding 'slug' column to podcasts table...")
            conn.execute("ALTER TABLE podcasts ADD COLUMN slug TEXT NOT NULL DEFAULT ''")
            logger.info("Added 'slug' column to podcasts table")

    # Check episodes table
    if not column_exists(conn, "episodes", "slug"):
        changes_needed = True
        if dry_run:
            logger.info("[DRY RUN] Would add 'slug' column to episodes table")
        else:
            logger.info("Adding 'slug' column to episodes table...")
            conn.execute("ALTER TABLE episodes ADD COLUMN slug TEXT NOT NULL DEFAULT ''")
            logger.info("Added 'slug' column to episodes table")

    return changes_needed


def create_indexes(conn: sqlite3.Connection, dry_run: bool = False):
    """Create indexes for slug columns if they don't exist."""
    indexes = [
        ("idx_podcasts_slug", "CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != ''"),
        (
            "idx_episodes_slug",
            "CREATE INDEX IF NOT EXISTS idx_episodes_slug ON episodes(podcast_id, slug) WHERE slug != ''",
        ),
    ]

    for index_name, create_sql in indexes:
        if dry_run:
            logger.info(f"[DRY RUN] Would create index: {index_name}")
        else:
            conn.execute(create_sql)
            logger.info(f"Created index: {index_name}")


def migrate_podcast_slugs(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    """Generate slugs for all podcasts without slugs."""
    # Check if slug column exists
    if not column_exists(conn, "podcasts", "slug"):
        if dry_run:
            # In dry run mode, column doesn't exist yet - get all podcasts
            cursor = conn.execute("SELECT id, title FROM podcasts")
            podcasts = [(row[0], row[1], None) for row in cursor.fetchall()]
            existing_slugs: Set[str] = set()
        else:
            logger.error("Slug column doesn't exist - run add_slug_columns first")
            return 0
    else:
        cursor = conn.execute("SELECT id, title, slug FROM podcasts WHERE slug = '' OR slug IS NULL")
        podcasts = cursor.fetchall()
        # Get existing slugs to handle collisions
        cursor = conn.execute("SELECT slug FROM podcasts WHERE slug != '' AND slug IS NOT NULL")
        existing_slugs = {row[0] for row in cursor.fetchall()}

    if not podcasts:
        logger.info("All podcasts already have slugs")
        return 0

    updated_count = 0
    for podcast_id, title, _ in podcasts:
        slug = generate_unique_slug(title, existing_slugs)
        existing_slugs.add(slug)

        if dry_run:
            logger.info(f"[DRY RUN] Would set podcast slug: '{title}' -> '{slug}'")
        else:
            conn.execute("UPDATE podcasts SET slug = ? WHERE id = ?", (slug, podcast_id))
            logger.debug(f"Set podcast slug: '{title}' -> '{slug}'")

        updated_count += 1

    if not dry_run:
        logger.info(f"Updated {updated_count} podcast slugs")

    return updated_count


def migrate_episode_slugs(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    """Generate slugs for all episodes without slugs."""
    # Check if slug column exists
    if not column_exists(conn, "episodes", "slug"):
        if dry_run:
            # In dry run mode, column doesn't exist yet - get all episodes
            cursor = conn.execute(
                """
                SELECT e.id, e.title, NULL as slug, e.podcast_id, p.title as podcast_title
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                ORDER BY e.podcast_id, e.pub_date DESC
            """
            )
            episodes = cursor.fetchall()
            existing_slugs_by_podcast: dict[str, Set[str]] = {}
        else:
            logger.error("Slug column doesn't exist - run add_slug_columns first")
            return 0
    else:
        # Get all episodes without slugs, grouped by podcast
        cursor = conn.execute(
            """
            SELECT e.id, e.title, e.slug, e.podcast_id, p.title as podcast_title
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.slug = '' OR e.slug IS NULL
            ORDER BY e.podcast_id, e.pub_date DESC
        """
        )
        episodes = cursor.fetchall()

        # Get existing slugs per podcast to handle collisions
        cursor = conn.execute(
            """
            SELECT podcast_id, slug
            FROM episodes
            WHERE slug != '' AND slug IS NOT NULL
        """
        )
        existing_slugs_by_podcast = {}
        for podcast_id, slug in cursor.fetchall():
            if podcast_id not in existing_slugs_by_podcast:
                existing_slugs_by_podcast[podcast_id] = set()
            existing_slugs_by_podcast[podcast_id].add(slug)

    if not episodes:
        logger.info("All episodes already have slugs")
        return 0

    updated_count = 0
    for episode_id, title, _, podcast_id, podcast_title in episodes:
        # Get or create slug set for this podcast
        if podcast_id not in existing_slugs_by_podcast:
            existing_slugs_by_podcast[podcast_id] = set()

        existing_slugs = existing_slugs_by_podcast[podcast_id]
        slug = generate_unique_slug(title, existing_slugs)
        existing_slugs.add(slug)

        if dry_run:
            logger.info(f"[DRY RUN] Would set episode slug: [{podcast_title}] '{title}' -> '{slug}'")
        else:
            conn.execute("UPDATE episodes SET slug = ? WHERE id = ?", (slug, episode_id))
            logger.debug(f"Set episode slug: [{podcast_title}] '{title}' -> '{slug}'")

        updated_count += 1

    if not dry_run:
        logger.info(f"Updated {updated_count} episode slugs")

    return updated_count


def run_migration(db_path: Path, dry_run: bool = False, backup: bool = False) -> bool:
    """Run the complete migration."""
    logger.info(f"Starting slug migration for database: {db_path}")

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return False

    # Backup if requested
    if backup and not dry_run:
        backup_database(db_path)

    # Connect to database
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Step 1: Add slug columns if missing
        logger.info("Step 1: Checking/adding slug columns...")
        columns_added = add_slug_columns(conn, dry_run)

        if not dry_run and columns_added:
            conn.commit()

        # Step 2: Generate slugs for podcasts
        logger.info("Step 2: Generating podcast slugs...")
        podcast_count = migrate_podcast_slugs(conn, dry_run)

        if not dry_run and podcast_count > 0:
            conn.commit()

        # Step 3: Generate slugs for episodes
        logger.info("Step 3: Generating episode slugs...")
        episode_count = migrate_episode_slugs(conn, dry_run)

        if not dry_run and episode_count > 0:
            conn.commit()

        # Step 4: Create indexes
        logger.info("Step 4: Creating indexes...")
        create_indexes(conn, dry_run)

        if not dry_run:
            conn.commit()

        # Summary
        if dry_run:
            logger.info("=" * 60)
            logger.info("DRY RUN SUMMARY:")
            logger.info(f"  - Columns to add: {'Yes' if columns_added else 'No (already exist)'}")
            logger.info(f"  - Podcasts to update: {podcast_count}")
            logger.info(f"  - Episodes to update: {episode_count}")
            logger.info("=" * 60)
            logger.info("Run without --dry-run to apply changes")
        else:
            logger.info("=" * 60)
            logger.info("MIGRATION COMPLETE:")
            logger.info(f"  - Columns added: {'Yes' if columns_added else 'No (already existed)'}")
            logger.info(f"  - Podcasts updated: {podcast_count}")
            logger.info(f"  - Episodes updated: {episode_count}")
            logger.info("=" * 60)

        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        conn.rollback()
        return False

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate database to add slug columns for podcasts and episodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/migrate_add_slugs.py --dry-run     Preview changes without applying
  python scripts/migrate_add_slugs.py --backup      Backup database before migration
  python scripts/migrate_add_slugs.py               Run migration
        """,
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to SQLite database (default: from config or data/podcasts.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create backup before migration",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    db_path = args.db_path or get_default_db_path()

    success = run_migration(db_path, dry_run=args.dry_run, backup=args.backup)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
