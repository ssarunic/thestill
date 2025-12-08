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
Migration script to rename existing files from sanitize_filename convention to slug-based naming.

Old convention: Podcast_Title_Episode_Title_{hash}.mp3 (underscores, special chars)
New convention: podcast-slug_episode-slug_{hash}.mp3 (hyphens, URL-safe)

This script:
1. Reads all episodes from SQLite database
2. For each episode with file paths set:
   - Generates new slug-based filename
   - Renames the physical file
   - Updates the database path
3. Handles all file types: original_audio, downsampled_audio, raw_transcripts, clean_transcripts, summaries

Usage:
    python scripts/migrate_filenames_to_slugs.py --dry-run    # Preview changes
    python scripts/migrate_filenames_to_slugs.py --backup     # Backup before migration
    python scripts/migrate_filenames_to_slugs.py              # Run migration
"""

import argparse
import hashlib
import logging
import os
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
class FileRename:
    """Represents a file rename operation."""

    episode_id: str
    db_field: str
    old_path: Path
    new_path: Path
    old_filename: str
    new_filename: str


@dataclass
class MigrationStats:
    """Statistics for the migration."""

    files_found: int = 0
    files_renamed: int = 0
    files_skipped: int = 0
    files_already_correct: int = 0
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
    backup_path = db_path.parent / f"{db_path.stem}_backup_migrate_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Database backed up to: {backup_path}")
    return backup_path


def extract_url_hash(filename: str) -> Optional[str]:
    """Extract the URL hash from an existing filename.

    Old format: Podcast_Title_Episode_Title_{hash}.ext
    The hash is the 8-character hex string before the extension.
    """
    # Remove extension
    name_without_ext = Path(filename).stem

    # Handle transcript suffix
    if name_without_ext.endswith("_transcript"):
        name_without_ext = name_without_ext[:-11]  # Remove "_transcript"

    # The hash should be the last 8 characters (hex)
    if len(name_without_ext) >= 8:
        potential_hash = name_without_ext[-8:]
        # Verify it looks like a hex hash
        if all(c in "0123456789abcdef" for c in potential_hash.lower()):
            return potential_hash.lower()

    return None


def generate_new_filename(
    podcast_slug: str,
    episode_slug: str,
    url_hash: str,
    extension: str,
    suffix: str = "",
) -> str:
    """Generate new slug-based filename.

    Format: {podcast_slug}_{episode_slug}_{hash}{suffix}.ext

    Args:
        podcast_slug: URL-safe podcast slug
        episode_slug: URL-safe episode slug
        url_hash: 8-char hash from audio URL
        extension: File extension including dot (e.g., ".mp3")
        suffix: Optional suffix like "_transcript", "_cleaned", "_summary"
    """
    base = f"{podcast_slug}_{episode_slug}_{url_hash}"
    return f"{base}{suffix}{extension}"


def get_file_mapping(
    conn: sqlite3.Connection,
    storage_path: Path,
) -> Tuple[List[FileRename], MigrationStats]:
    """Build list of files to rename."""
    stats = MigrationStats()
    renames: List[FileRename] = []

    # File type configurations: (db_field, subdir, suffix)
    # suffix is the part between hash and extension in new filename
    file_configs = [
        ("audio_path", "original_audio", ""),
        ("downsampled_audio_path", "downsampled_audio", ""),
        ("raw_transcript_path", "raw_transcripts", "_transcript"),
        ("clean_transcript_path", "clean_transcripts", "_cleaned"),
        ("summary_path", "summaries", "_summary"),
    ]

    # Get all episodes with their podcast info
    cursor = conn.execute(
        """
        SELECT e.id, e.title, e.slug as e_slug, e.audio_url,
               e.audio_path, e.downsampled_audio_path, e.raw_transcript_path,
               e.clean_transcript_path, e.summary_path,
               p.title as p_title, p.slug as p_slug
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        ORDER BY p.title, e.pub_date DESC
    """
    )

    for row in cursor.fetchall():
        episode_id = row[0]
        episode_slug = row[2] or ""
        audio_url = row[3]
        podcast_slug = row[10] or ""

        if not podcast_slug or not episode_slug:
            logger.warning(f"Episode {episode_id} missing slug, skipping")
            stats.files_skipped += 1
            continue

        # Generate URL hash from audio_url (same as AudioDownloader does)
        url_hash = hashlib.md5(audio_url.encode()).hexdigest()[:8]

        # Check each file type
        for db_field, subdir, suffix in file_configs:
            # Get current path from row (indices 4-8 correspond to file paths)
            field_index = {
                "audio_path": 4,
                "downsampled_audio_path": 5,
                "raw_transcript_path": 6,
                "clean_transcript_path": 7,
                "summary_path": 8,
            }[db_field]
            current_filename = row[field_index]

            if not current_filename:
                continue

            stats.files_found += 1

            # Build full paths
            directory = storage_path / subdir
            old_path = directory / current_filename

            # Check if file exists
            if not old_path.exists():
                logger.warning(f"File not found: {old_path}")
                stats.files_missing += 1
                continue

            # Get extension
            extension = old_path.suffix

            # Generate new filename
            new_filename = generate_new_filename(podcast_slug, episode_slug, url_hash, extension, suffix)
            new_path = directory / new_filename

            # Check if already correct
            if current_filename == new_filename:
                stats.files_already_correct += 1
                continue

            # Check for conflicts
            if new_path.exists() and new_path != old_path:
                logger.warning(f"Target file already exists: {new_path}")
                stats.errors += 1
                continue

            renames.append(
                FileRename(
                    episode_id=episode_id,
                    db_field=db_field,
                    old_path=old_path,
                    new_path=new_path,
                    old_filename=current_filename,
                    new_filename=new_filename,
                )
            )

    return renames, stats


def execute_renames(
    conn: sqlite3.Connection,
    renames: List[FileRename],
    dry_run: bool = False,
) -> int:
    """Execute file renames and database updates."""
    renamed_count = 0

    # Group renames by episode for efficient DB updates
    by_episode: Dict[str, List[FileRename]] = {}
    for rename in renames:
        if rename.episode_id not in by_episode:
            by_episode[rename.episode_id] = []
        by_episode[rename.episode_id].append(rename)

    for episode_id, episode_renames in by_episode.items():
        try:
            # Rename all files for this episode
            for rename in episode_renames:
                if dry_run:
                    logger.info(f"[DRY RUN] Would rename:")
                    logger.info(f"  {rename.old_filename}")
                    logger.info(f"  -> {rename.new_filename}")
                else:
                    # Rename the file
                    rename.old_path.rename(rename.new_path)
                    logger.debug(f"Renamed: {rename.old_filename} -> {rename.new_filename}")

            # Update database for all fields in this episode
            if not dry_run:
                for rename in episode_renames:
                    conn.execute(
                        f"UPDATE episodes SET {rename.db_field} = ? WHERE id = ?",
                        (rename.new_filename, episode_id),
                    )
                conn.commit()

            renamed_count += len(episode_renames)

        except Exception as e:
            logger.error(f"Error processing episode {episode_id}: {e}")
            # Rollback any partial changes for this episode
            if not dry_run:
                conn.rollback()
                # Try to restore any already-renamed files
                for rename in episode_renames:
                    if rename.new_path.exists() and not rename.old_path.exists():
                        try:
                            rename.new_path.rename(rename.old_path)
                        except Exception:
                            pass

    return renamed_count


def run_migration(
    db_path: Path,
    storage_path: Path,
    dry_run: bool = False,
    backup: bool = False,
) -> bool:
    """Run the complete migration."""
    logger.info(f"Starting filename migration")
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
        logger.info("Analyzing files to rename...")
        renames, stats = get_file_mapping(conn, storage_path)

        logger.info(f"Files found in database: {stats.files_found}")
        logger.info(f"Files already correct: {stats.files_already_correct}")
        logger.info(f"Files missing: {stats.files_missing}")
        logger.info(f"Files to rename: {len(renames)}")

        if not renames:
            logger.info("No files need renaming!")
            return True

        # Show sample renames
        logger.info("\nSample renames:")
        for rename in renames[:5]:
            logger.info(f"  {rename.old_filename[:60]}...")
            logger.info(f"  -> {rename.new_filename}")
            logger.info("")

        if len(renames) > 5:
            logger.info(f"  ... and {len(renames) - 5} more")

        # Execute renames
        logger.info("\nExecuting renames...")
        renamed_count = execute_renames(conn, renames, dry_run)

        # Summary
        logger.info("=" * 60)
        if dry_run:
            logger.info("DRY RUN SUMMARY:")
            logger.info(f"  Files that would be renamed: {renamed_count}")
        else:
            logger.info("MIGRATION COMPLETE:")
            logger.info(f"  Files renamed: {renamed_count}")
        logger.info("=" * 60)

        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return False

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate filenames from sanitize_filename to slug-based naming",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/migrate_filenames_to_slugs.py --dry-run     Preview changes
  python scripts/migrate_filenames_to_slugs.py --backup      Backup and migrate
  python scripts/migrate_filenames_to_slugs.py               Run migration
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
