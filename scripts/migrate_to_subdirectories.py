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
Migration script to reorganize files from flat structure to podcast subdirectories.

Old structure: data/original_audio/podcast-slug_episode-slug_hash.mp3
New structure: data/original_audio/podcast-slug/episode-slug_hash.mp3

This script:
1. Reads all episodes from SQLite database
2. For each episode with file paths:
   - Extracts podcast_slug from the filename prefix
   - Creates podcast subdirectory if needed
   - Moves the file to the subdirectory
   - Updates the database path to use relative path format

Supports: original_audio, downsampled_audio, raw_transcripts

Usage:
    python scripts/migrate_to_subdirectories.py --dry-run    # Preview changes
    python scripts/migrate_to_subdirectories.py --backup     # Backup before migration
    python scripts/migrate_to_subdirectories.py              # Run migration
"""

import argparse
import logging
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
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
    db_field: str
    old_path: Path
    new_path: Path
    old_db_value: str
    new_db_value: str


@dataclass
class MigrationStats:
    """Statistics for the migration."""

    files_found: int = 0
    files_moved: int = 0
    files_already_migrated: int = 0
    files_missing: int = 0
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


def backup_database(db_path: Path) -> Path:
    """Create a backup of the database."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.stem}_backup_subdir_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Database backed up to: {backup_path}")
    return backup_path


def is_already_in_subdirectory(path_value: str) -> bool:
    """Check if a path is already in subdirectory format (contains /)."""
    return "/" in path_value


def extract_podcast_slug_from_filename(filename: str, podcast_slug: str) -> Optional[str]:
    """
    Extract podcast slug from a filename that follows the convention:
    {podcast_slug}_{episode_slug}_{hash}.ext

    We use the podcast_slug from the database to verify and extract.
    """
    # Check if filename starts with the podcast slug
    if filename.startswith(f"{podcast_slug}_"):
        return podcast_slug
    return None


def get_new_filename(old_filename: str, podcast_slug: str) -> Optional[str]:
    """
    Convert flat filename to just the episode part.

    Old: podcast-slug_episode-slug_hash.ext
    New: episode-slug_hash.ext
    """
    prefix = f"{podcast_slug}_"
    if old_filename.startswith(prefix):
        return old_filename[len(prefix) :]
    return None


def get_file_moves(
    conn: sqlite3.Connection,
    storage_path: Path,
) -> Tuple[List[FileMove], MigrationStats]:
    """Build list of files to move."""
    stats = MigrationStats()
    moves: List[FileMove] = []

    # File type configurations: (db_field, subdir)
    file_configs = [
        ("audio_path", "original_audio"),
        ("downsampled_audio_path", "downsampled_audio"),
        ("raw_transcript_path", "raw_transcripts"),
    ]

    # Get all episodes with their podcast info
    cursor = conn.execute(
        """
        SELECT e.id, e.title, e.slug as e_slug,
               e.audio_path, e.downsampled_audio_path, e.raw_transcript_path,
               p.title as p_title, p.slug as p_slug
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        ORDER BY p.title, e.pub_date DESC
    """
    )

    for row in cursor.fetchall():
        episode_id = row[0]
        episode_slug = row[2] or ""
        podcast_slug = row[7] or ""  # Index 7 after adding raw_transcript_path

        if not podcast_slug:
            logger.warning(f"Episode {episode_id} missing podcast slug, skipping")
            stats.errors += 1
            continue

        # Check each file type
        for db_field, subdir in file_configs:
            # Get current path from row
            field_index = {"audio_path": 3, "downsampled_audio_path": 4, "raw_transcript_path": 5}[db_field]
            current_value = row[field_index]

            if not current_value:
                continue

            stats.files_found += 1

            # Check if already in subdirectory format
            if is_already_in_subdirectory(current_value):
                stats.files_already_migrated += 1
                continue

            # Build full paths
            directory = storage_path / subdir
            old_path = directory / current_value

            # Check if file exists
            if not old_path.exists():
                logger.warning(f"File not found: {old_path}")
                stats.files_missing += 1
                continue

            # Get new filename (strip podcast prefix)
            new_filename = get_new_filename(current_value, podcast_slug)
            if not new_filename:
                # Filename doesn't follow expected convention, use as-is in subdirectory
                new_filename = current_value
                logger.info(f"Filename doesn't match convention, moving as-is: {current_value}")

            # Build new path
            new_dir = directory / podcast_slug
            new_path = new_dir / new_filename
            new_db_value = f"{podcast_slug}/{new_filename}"

            # Check for conflicts
            if new_path.exists() and new_path != old_path:
                logger.warning(f"Target file already exists: {new_path}")
                stats.errors += 1
                continue

            moves.append(
                FileMove(
                    episode_id=episode_id,
                    db_field=db_field,
                    old_path=old_path,
                    new_path=new_path,
                    old_db_value=current_value,
                    new_db_value=new_db_value,
                )
            )

    return moves, stats


def execute_moves(
    conn: sqlite3.Connection,
    moves: List[FileMove],
    dry_run: bool = False,
) -> int:
    """Execute file moves and database updates."""
    moved_count = 0

    # Group moves by episode for efficient DB updates
    by_episode: Dict[str, List[FileMove]] = {}
    for move in moves:
        if move.episode_id not in by_episode:
            by_episode[move.episode_id] = []
        by_episode[move.episode_id].append(move)

    for episode_id, episode_moves in by_episode.items():
        try:
            # Move all files for this episode
            for move in episode_moves:
                if dry_run:
                    logger.info(f"[DRY RUN] Would move:")
                    logger.info(f"  {move.old_path}")
                    logger.info(f"  -> {move.new_path}")
                else:
                    # Create subdirectory if needed
                    move.new_path.parent.mkdir(parents=True, exist_ok=True)

                    # Move the file
                    shutil.move(str(move.old_path), str(move.new_path))
                    logger.debug(f"Moved: {move.old_db_value} -> {move.new_db_value}")

            # Update database for all fields in this episode
            if not dry_run:
                for move in episode_moves:
                    conn.execute(
                        f"UPDATE episodes SET {move.db_field} = ? WHERE id = ?",
                        (move.new_db_value, episode_id),
                    )
                conn.commit()

            moved_count += len(episode_moves)

        except Exception as e:
            logger.error(f"Error processing episode {episode_id}: {e}")
            # Rollback any partial changes for this episode
            if not dry_run:
                conn.rollback()
                # Try to restore any already-moved files
                for move in episode_moves:
                    if move.new_path.exists() and not move.old_path.exists():
                        try:
                            shutil.move(str(move.new_path), str(move.old_path))
                        except Exception:
                            pass

    return moved_count


def run_migration(
    db_path: Path,
    storage_path: Path,
    dry_run: bool = False,
    backup: bool = False,
) -> bool:
    """Run the complete migration."""
    logger.info(f"Starting subdirectory migration")
    logger.info(f"  Database: {db_path}")
    logger.info(f"  Storage: {storage_path}")
    logger.info(f"  Dry run: {dry_run}")

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
        # Build file mapping
        logger.info("Analyzing files to move...")
        moves, stats = get_file_moves(conn, storage_path)

        logger.info(f"Files found in database: {stats.files_found}")
        logger.info(f"Files already in subdirectories: {stats.files_already_migrated}")
        logger.info(f"Files missing: {stats.files_missing}")
        logger.info(f"Files to move: {len(moves)}")

        if not moves:
            logger.info("No files need moving!")
            return True

        # Show sample moves
        logger.info("\nSample moves:")
        for move in moves[:5]:
            logger.info(f"  {move.old_db_value}")
            logger.info(f"  -> {move.new_db_value}")
            logger.info("")

        if len(moves) > 5:
            logger.info(f"  ... and {len(moves) - 5} more")

        # Execute moves
        logger.info("\nExecuting moves...")
        moved_count = execute_moves(conn, moves, dry_run)

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
        description="Migrate files from flat structure to podcast subdirectories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/migrate_to_subdirectories.py --dry-run     Preview changes
  python scripts/migrate_to_subdirectories.py --backup      Backup and migrate
  python scripts/migrate_to_subdirectories.py               Run migration
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
    storage_path = args.storage_path or get_default_storage_path()

    success = run_migration(
        db_path,
        storage_path,
        dry_run=args.dry_run,
        backup=args.backup,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
