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
Migration script to reorganize episode facts from UUID-based to slug-based naming.

Old structure: data/episode_facts/{uuid}.facts.md
New structure: data/episode_facts/{podcast_slug}/{episode_slug}.facts.md

This script:
1. Reads all episode facts files (UUID-based)
2. Looks up the episode in the database to get podcast_slug and episode_slug
3. Moves the file to the new location
4. Creates podcast subdirectories as needed

Usage:
    python scripts/migrate_episode_facts_to_slugs.py --dry-run    # Preview changes
    python scripts/migrate_episode_facts_to_slugs.py              # Run migration
"""

import argparse
import logging
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class FileMove:
    """Represents a file move operation."""

    episode_id: str
    old_path: Path
    new_path: Path


@dataclass
class MigrationStats:
    """Statistics for the migration."""

    files_found: int = 0
    files_moved: int = 0
    files_already_migrated: int = 0
    files_not_in_db: int = 0
    errors: int = 0


def get_default_db_path() -> Path:
    """Get default database path from config or fallback."""
    try:
        from thestill.utils.config import Config

        config = Config()
        return Path(config.database_path)
    except Exception:
        return Path("data/podcasts.db")


def get_default_storage_path() -> Path:
    """Get default storage path."""
    try:
        from thestill.utils.config import Config

        config = Config()
        return Path(config.storage_path)
    except Exception:
        return Path("data")


def is_uuid_filename(filename: str) -> bool:
    """Check if filename looks like a UUID (36 chars with hyphens)."""
    # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.facts.md
    name = filename.replace(".facts.md", "")
    if len(name) != 36:
        return False
    parts = name.split("-")
    if len(parts) != 5:
        return False
    expected_lengths = [8, 4, 4, 4, 12]
    return all(len(p) == l for p, l in zip(parts, expected_lengths))


def get_episode_info(conn: sqlite3.Connection, episode_id: str) -> Optional[Tuple[str, str]]:
    """
    Look up episode in database to get podcast_slug and episode_slug.

    Returns:
        Tuple of (podcast_slug, episode_slug) or None if not found
    """
    cursor = conn.execute(
        """
        SELECT p.slug, e.slug
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE e.id = ?
    """,
        (episode_id,),
    )
    row = cursor.fetchone()
    if row and row[0] and row[1]:
        return (row[0], row[1])
    return None


def get_file_moves(
    conn: sqlite3.Connection,
    episode_facts_dir: Path,
) -> Tuple[List[FileMove], MigrationStats]:
    """Build list of files to move."""
    stats = MigrationStats()
    moves: List[FileMove] = []

    if not episode_facts_dir.exists():
        logger.info("Episode facts directory does not exist")
        return moves, stats

    # Find all UUID-based facts files
    for file_path in episode_facts_dir.glob("*.facts.md"):
        if not file_path.is_file():
            continue

        filename = file_path.name
        if not is_uuid_filename(filename):
            # Skip non-UUID files (might be already migrated or other format)
            continue

        stats.files_found += 1
        episode_id = filename.replace(".facts.md", "")

        # Look up episode info in database
        episode_info = get_episode_info(conn, episode_id)
        if not episode_info:
            logger.warning(f"Episode not found in database: {episode_id}")
            stats.files_not_in_db += 1
            continue

        podcast_slug, episode_slug = episode_info

        # Build new path
        new_dir = episode_facts_dir / podcast_slug
        new_path = new_dir / f"{episode_slug}.facts.md"

        # Check if already exists at new location
        if new_path.exists():
            logger.info(f"Already exists at new location: {new_path}")
            stats.files_already_migrated += 1
            continue

        moves.append(
            FileMove(
                episode_id=episode_id,
                old_path=file_path,
                new_path=new_path,
            )
        )

    return moves, stats


def execute_moves(
    moves: List[FileMove],
    dry_run: bool = False,
) -> int:
    """Execute file moves."""
    moved_count = 0

    for move in moves:
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would move:")
                logger.info(f"  {move.old_path.name}")
                logger.info(f"  -> {move.new_path.parent.name}/{move.new_path.name}")
            else:
                # Create podcast subdirectory if needed
                move.new_path.parent.mkdir(parents=True, exist_ok=True)

                # Move the file
                shutil.move(str(move.old_path), str(move.new_path))
                logger.debug(f"Moved: {move.old_path.name} -> {move.new_path}")

            moved_count += 1

        except Exception as e:
            logger.error(f"Error moving {move.old_path}: {e}")

    return moved_count


def run_migration(
    db_path: Path,
    storage_path: Path,
    dry_run: bool = False,
) -> bool:
    """Run the complete migration."""
    logger.info(f"Starting episode facts migration")
    logger.info(f"  Database: {db_path}")
    logger.info(f"  Storage: {storage_path}")
    logger.info(f"  Dry run: {dry_run}")

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return False

    episode_facts_dir = storage_path / "episode_facts"

    # Connect to database
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Build file mapping
        logger.info("Analyzing files to move...")
        moves, stats = get_file_moves(conn, episode_facts_dir)

        logger.info(f"UUID-based files found: {stats.files_found}")
        logger.info(f"Files already in subdirectories: {stats.files_already_migrated}")
        logger.info(f"Files not in database: {stats.files_not_in_db}")
        logger.info(f"Files to move: {len(moves)}")

        if not moves:
            logger.info("No files need moving!")
            return True

        # Show sample moves
        logger.info("\nSample moves:")
        for move in moves[:5]:
            logger.info(f"  {move.old_path.name}")
            logger.info(f"  -> {move.new_path.parent.name}/{move.new_path.name}")
            logger.info("")

        if len(moves) > 5:
            logger.info(f"  ... and {len(moves) - 5} more")

        # Execute moves
        logger.info("\nExecuting moves...")
        moved_count = execute_moves(moves, dry_run)

        # Summary
        logger.info("=" * 60)
        if dry_run:
            logger.info("DRY RUN SUMMARY:")
            logger.info(f"  Files that would be moved: {moved_count}")
        else:
            logger.info("MIGRATION COMPLETE:")
            logger.info(f"  Files moved: {moved_count}")
        logger.info("=" * 60)

        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return False

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate episode facts from UUID-based to slug-based naming",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/migrate_episode_facts_to_slugs.py --dry-run     Preview changes
  python scripts/migrate_episode_facts_to_slugs.py               Run migration
        """,
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to SQLite database (default: from config or data/podcasts.db)",
    )
    parser.add_argument(
        "--storage-path",
        type=Path,
        default=None,
        help="Path to storage directory (default: from config or data/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
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
    storage_path = args.storage_path or get_default_storage_path()

    success = run_migration(
        db_path,
        storage_path,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
