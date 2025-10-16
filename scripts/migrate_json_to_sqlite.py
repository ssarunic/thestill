#!/usr/bin/env python3
"""
Migrate podcast data from JSON (feeds.json) to SQLite database.

This is a one-time migration script to convert existing JSON-based storage
to the new SQLite backend.

Usage:
    python scripts/migrate_json_to_sqlite.py [--json-file PATH] [--db-file PATH] [--dry-run]

Examples:
    # Migrate with default paths
    python scripts/migrate_json_to_sqlite.py

    # Specify custom paths
    python scripts/migrate_json_to_sqlite.py --json-file ./data/feeds.json --db-file ./data/podcasts.db

    # Dry run (preview without changes)
    python scripts/migrate_json_to_sqlite.py --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

# Module-level logger (will be configured in main())
logger = logging.getLogger(__name__)


def load_json_data(json_file: Path) -> List[Podcast]:
    """
    Load podcast data from JSON file.

    Args:
        json_file: Path to feeds.json

    Returns:
        List of Podcast objects

    Raises:
        FileNotFoundError: If JSON file doesn't exist
        ValueError: If JSON is malformed
    """
    if not json_file.exists():
        raise FileNotFoundError(f"JSON file not found: {json_file}")

    logger.info(f"Loading data from {json_file}")

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON file: {e}") from e

    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")

    podcasts = []
    for item in data:
        try:
            podcast = Podcast(**item)
            podcasts.append(podcast)
        except Exception as e:
            logger.error(f"Failed to parse podcast {item.get('title', 'unknown')}: {e}")
            raise

    logger.info(f"Loaded {len(podcasts)} podcasts from JSON")
    return podcasts


def migrate_to_sqlite(podcasts: List[Podcast], db_file: Path, dry_run: bool = False) -> None:
    """
    Migrate podcasts to SQLite database.

    Args:
        podcasts: List of podcasts to migrate
        db_file: Path to SQLite database file
        dry_run: If True, only preview without writing

    Raises:
        Exception: If migration fails
    """
    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
        logger.info(f"Would migrate {len(podcasts)} podcasts to {db_file}")

        total_episodes = sum(len(p.episodes) for p in podcasts)
        logger.info(f"Total episodes: {total_episodes}")

        for podcast in podcasts:
            logger.info(f"  - {podcast.title} ({len(podcast.episodes)} episodes)")

        return

    # Check if database already exists
    if db_file.exists():
        logger.warning(f"Database already exists: {db_file}")
        response = input("Overwrite existing database? [y/N]: ")
        if response.lower() != "y":
            logger.info("Migration cancelled")
            return

        # Backup existing database
        backup_file = db_file.with_suffix(".db.backup")
        logger.info(f"Creating backup: {backup_file}")
        import shutil

        shutil.copy2(db_file, backup_file)

        # Remove old database
        db_file.unlink()

    # Create repository and migrate
    logger.info(f"Creating SQLite database: {db_file}")
    repo = SqlitePodcastRepository(db_path=str(db_file))

    logger.info("Migrating podcasts...")
    for i, podcast in enumerate(podcasts, 1):
        logger.info(f"[{i}/{len(podcasts)}] Migrating: {podcast.title} ({len(podcast.episodes)} episodes)")
        try:
            repo.save(podcast)
        except Exception as e:
            logger.error(f"Failed to migrate podcast '{podcast.title}': {e}")
            raise

    logger.info("Migration completed successfully!")

    # Verify migration
    logger.info("Verifying migration...")
    all_podcasts = repo.get_all()
    total_episodes = sum(len(p.episodes) for p in all_podcasts)

    logger.info(f"Verification: {len(all_podcasts)} podcasts, {total_episodes} episodes in database")

    if len(all_podcasts) != len(podcasts):
        logger.error(f"Mismatch! Expected {len(podcasts)} podcasts, found {len(all_podcasts)}")
        raise RuntimeError("Migration verification failed")

    logger.info("✓ Migration verified successfully")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate podcast data from JSON to SQLite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--json-file", type=Path, default=Path("data/feeds.json"), help="Path to JSON file (default: data/feeds.json)"
    )

    parser.add_argument(
        "--db-file",
        type=Path,
        default=Path("data/podcasts.db"),
        help="Path to SQLite database (default: data/podcasts.db)",
    )

    parser.add_argument("--dry-run", action="store_true", help="Preview migration without making changes")

    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout)

    try:
        # Load JSON data
        podcasts = load_json_data(args.json_file)

        # Migrate to SQLite
        migrate_to_sqlite(podcasts, args.db_file, dry_run=args.dry_run)

        if not args.dry_run:
            logger.info("")
            logger.info("=" * 70)
            logger.info("MIGRATION COMPLETE")
            logger.info("=" * 70)
            logger.info(f"Old JSON file: {args.json_file}")
            logger.info(f"New SQLite DB: {args.db_file}")
            logger.info("")
            logger.info("Next steps:")
            logger.info("  1. Verify the SQLite database works: thestill list")
            logger.info(f"  2. Keep JSON backup: {args.json_file}")
            logger.info("  3. The system will now use SQLite automatically")
            logger.info("")

        return 0

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Invalid data: {e}")
        return 1
    except KeyboardInterrupt:
        logger.warning("Migration cancelled by user")
        return 130
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
