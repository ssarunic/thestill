# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""One-off repair: backfill segmented-transcript JSON sidecars.

Background
----------
A handful of episodes were cleaned before (or were skipped by) the spec #18
segmented cleanup pipeline. They have a legacy blended Markdown transcript
(``clean_transcript_path``) but no ``AnnotatedTranscript`` JSON sidecar
(``clean_transcript_json_path`` is NULL), so the web viewer falls back to
the legacy "blended" tab for them. Their raw transcripts are *not*
degenerate — they carry hundreds of real segments with word-level
timestamps — so re-running the segmented cleaner produces a proper sidecar
and lets these episodes use the segmented viewer like everything else.

What this does
--------------
For each target episode it re-runs ``TranscriptCleaningProcessor`` (the same
two-pass facts + segmented pipeline the CLI ``clean-transcript`` command
uses), writes the cleaned Markdown + JSON sidecar to disk, and updates the
episode's ``clean_transcript_path`` / ``clean_transcript_json_path`` rows.

This re-clean spends LLM tokens (Pass 1 facts extraction + Pass 2 segmented
cleanup) against whatever provider ``.env`` configures (``LLM_PROVIDER``).

Run
---
    ./venv/bin/python scripts/backfill_segmented_transcripts.py            # dry-run (default)
    ./venv/bin/python scripts/backfill_segmented_transcripts.py --apply    # write
    ./venv/bin/python scripts/backfill_segmented_transcripts.py --apply --episode-id <uuid> ...
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.llm_provider import create_llm_provider_from_config
from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor
from thestill.logging import configure_structlog
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.config import load_config
from thestill.utils.path_manager import PathManager

# The five episodes that have legacy blended Markdown but no segmented JSON
# sidecar. All five were verified to have rich, non-degenerate raw
# transcripts (hundreds of segments, word-level timestamps present).
DEFAULT_EPISODE_IDS = [
    "07c9e9b1-0c0c-4f07-87a3-1e711ee8ad2e",  # Dwarkesh — Andrej Karpathy AGI
    "354b3d06-0be8-4c73-8b59-dc13c8883fb5",  # Lenny — Gamma $100M ARR
    "76ad3de5-b2e3-4402-98f7-83386a9c5bd3",  # Moonshots — AI Wealth Gap
    "84bbb23d-9a77-4301-8e74-839e8459fe05",  # Mjesto Zločina — Epizoda 192 Halloween
    "de507ae1-b99a-4104-a269-fcb20b8a3b48",  # Mjesto Zločina — Epizoda 195 Joseph Kallinger
]


def _find_targets(
    feed_manager: PodcastFeedManager,
    episode_ids: List[str],
) -> List[Tuple[Podcast, Episode]]:
    """Resolve (podcast, episode) pairs for the requested episode ids."""
    wanted = set(episode_ids)
    found: List[Tuple[Podcast, Episode]] = []
    for podcast in feed_manager.list_podcasts():
        for episode in podcast.episodes:
            if episode.id in wanted:
                found.append((podcast, episode))
                wanted.discard(episode.id)
    if wanted:
        missing = ", ".join(sorted(wanted))
        raise SystemExit(f"❌ Episode id(s) not found in DB: {missing}")
    return found


def _derive_paths(path_manager: PathManager, podcast: Podcast, transcript_path: Path) -> Tuple[Path, str]:
    """Mirror the CLI's clean-transcript output path derivation."""
    base_name = transcript_path.stem
    if base_name.endswith("_transcript"):
        base_name = base_name[: -len("_transcript")]
    parts = base_name.split("_")
    if len(parts) >= 3:
        episode_slug_hash = "_".join(parts[1:])
    else:
        episode_slug_hash = base_name

    podcast_subdir = path_manager.clean_transcripts_dir() / podcast.slug
    podcast_subdir.mkdir(parents=True, exist_ok=True)
    cleaned_filename = f"{episode_slug_hash}_cleaned.md"
    cleaned_path = podcast_subdir / cleaned_filename
    clean_transcript_db_path = f"{podcast.slug}/{cleaned_filename}"
    return cleaned_path, clean_transcript_db_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually re-clean and write (default is a dry-run).",
    )
    parser.add_argument(
        "--episode-id",
        dest="episode_ids",
        action="append",
        help="Episode id to backfill (repeatable). Defaults to the known 5.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-clean even episodes that already have a JSON sidecar. Without "
            "this, episodes with a non-NULL clean_transcript_json_path are "
            "skipped (the default 5 are already backfilled in this workspace, "
            "so a bare --apply would re-spend LLM tokens and overwrite them)."
        ),
    )
    args = parser.parse_args()

    episode_ids = args.episode_ids or DEFAULT_EPISODE_IDS

    load_dotenv()
    configure_structlog()
    config = load_config()
    path_manager = PathManager(str(config.storage_path))
    repository = SqlitePodcastRepository(db_path=config.database_path)
    feed_manager = PodcastFeedManager(
        repository,
        path_manager,
        max_workers=config.refresh_max_workers,
        max_per_host=config.refresh_max_per_host,
    )

    targets = _find_targets(feed_manager, episode_ids)

    print(f"🎯 {len(targets)} episode(s) targeted for segmented backfill:")
    for podcast, episode in targets:
        has_json = bool(episode.clean_transcript_json_path)
        raw = path_manager.raw_transcript_file(episode.raw_transcript_path) if episode.raw_transcript_path else None
        raw_ok = raw.exists() if raw else False
        # Mirror the --apply decision so the dry-run shows what would happen:
        # episodes that already have a sidecar are skipped unless --force.
        if has_json and not args.force:
            action = "skip (has sidecar; --force to re-clean)"
        elif not raw_ok:
            action = "skip (raw missing)"
        else:
            action = "re-clean"
        print(
            f"  • [{episode.id[:8]}] {podcast.title} — {episode.title[:50]}"
            f"  (json_sidecar={'present' if has_json else 'MISSING'}, raw={'ok' if raw_ok else 'MISSING'}"
            f" → {action})"
        )

    if not args.apply:
        print("\n(dry-run — re-run with --apply to re-clean and write sidecars)")
        return

    llm_provider = create_llm_provider_from_config(config)
    print(f"\n✓ Using {config.llm_provider.upper()} provider: {llm_provider.get_model_name()}")
    cleaning_processor = TranscriptCleaningProcessor(llm_provider)

    succeeded = 0
    skipped = 0
    failed: List[str] = []
    start = time.time()

    for podcast, episode in targets:
        print("\n" + "─" * 60)
        print(f"📻 {podcast.title}")
        print(f"🎧 {episode.title}")

        # P1 — this is a backfill for episodes MISSING a sidecar. Re-cleaning an
        # episode that already has one re-spends LLM tokens and overwrites a
        # good artifact, so skip it unless the caller explicitly forces it.
        if episode.clean_transcript_json_path and not args.force:
            print("  ⏭️  already has a JSON sidecar; skipping (use --force to re-clean)")
            skipped += 1
            continue

        # P2 — raw_transcript_path is Optional on the model. A custom
        # --episode-id may point at a row without one; guard here so it joins
        # the per-episode failure path instead of crashing the whole run when
        # raw_transcript_file() is handed None.
        if not episode.raw_transcript_path:
            print("  ❌ no raw transcript path on episode record")
            failed.append(episode.id)
            continue

        transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
        if not transcript_path.exists():
            print(f"  ❌ raw transcript missing: {transcript_path}")
            failed.append(episode.id)
            continue

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)

            cleaned_path, clean_transcript_db_path = _derive_paths(path_manager, podcast, transcript_path)

            result = cleaning_processor.clean_transcript(
                transcript_data=transcript_data,
                podcast_title=podcast.title,
                podcast_description=podcast.description,
                episode_title=episode.title,
                episode_description=episode.description,
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                output_path=str(cleaned_path),
                path_manager=path_manager,
                language=podcast.language,
            )

            if not result or not result.get("cleaned_json_path"):
                print("  ❌ cleaning produced no JSON sidecar")
                failed.append(episode.id)
                continue

            json_filename = f"{Path(cleaned_path.name).stem}.json"
            clean_transcript_json_db_path = f"{podcast.slug}/{json_filename}"

            feed_manager.mark_episode_processed(
                str(podcast.rss_url),
                episode.external_id,
                raw_transcript_path=episode.raw_transcript_path,
                clean_transcript_path=clean_transcript_db_path,
                clean_transcript_json_path=clean_transcript_json_db_path,
            )

            succeeded += 1
            print(f"  ✅ sidecar written: {clean_transcript_json_db_path}")
            print(f"  👥 speakers: {len(result['episode_facts'].speaker_mapping)}")

        except Exception as exc:  # noqa: BLE001 — surface any failure per-episode
            import traceback

            print(f"  ❌ error: {exc}")
            traceback.print_exc()
            failed.append(episode.id)
            continue

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"🎉 Done: {succeeded}/{len(targets)} backfilled in {elapsed:.1f}s")
    if skipped:
        print(f"⏭️  Skipped (already had a sidecar; --force to re-clean): {skipped}")
    if failed:
        print(f"⚠️  Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
