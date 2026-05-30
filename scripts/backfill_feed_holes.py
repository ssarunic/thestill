# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""One-off repair: backfill "holes" left in feeds by the old refresh cap.

Background
----------
Until the gate added in ``feed_manager._refresh_single_podcast``, every
refresh — new *and* existing podcasts — capped discovery at
``MAX_EPISODES_PER_PODCAST`` newest entries, then advanced
``last_processed`` past the trimmed ones. When a feed published more new
episodes between refreshes than the cap, the surplus was never inserted and
``last_processed`` moved past their ``pub_date`` — so an incremental refresh
can never re-discover them. The result is a permanent gap (a "hole") in the
middle of an otherwise-tracked feed.

What this does
--------------
For each (non-synthetic) RSS-backed podcast it refetches the *full* feed,
bypassing both the incremental ``last_processed`` filter and the cap, and
inserts only the missing entries whose ``pub_date`` falls **within** the
already-tracked range (i.e. newer than the oldest episode we already have).
Older back-catalogue that predates when you started tracking the podcast is
*not* a hole and is left alone. ``last_processed`` is never modified — the
holes are older than it, so inserting bypasses the date filter entirely.

Inserts go through ``repository.save_episodes`` which is idempotent on
``(podcast_id, external_id)``, so re-running is safe.

Run
---
    ./venv/bin/python scripts/backfill_feed_holes.py            # dry-run (default)
    ./venv/bin/python scripts/backfill_feed_holes.py --apply    # write inserts
    ./venv/bin/python scripts/backfill_feed_holes.py --podcast-id <uuid> --apply
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Set

from thestill.core.media_source import RSSMediaSource
from thestill.models.podcast import Episode
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.datetime_utils import ensure_utc

DB = "data/podcasts.db"


def _is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u


def _load_podcasts(con: sqlite3.Connection, podcast_id: Optional[str]) -> List[sqlite3.Row]:
    sql = "SELECT id, title, slug, rss_url, last_processed FROM podcasts WHERE synthetic = 0"
    params: tuple = ()
    if podcast_id:
        sql += " AND id = ?"
        params = (podcast_id,)
    return con.execute(sql + " ORDER BY title", params).fetchall()


def _tracked(con: sqlite3.Connection, pid: str) -> tuple[Set[str], Optional[datetime]]:
    """Return (known external_ids, oldest tracked pub_date) for a podcast."""
    rows = con.execute("SELECT external_id, pub_date FROM episodes WHERE podcast_id = ?", (pid,)).fetchall()
    known = {r["external_id"] for r in rows if r["external_id"]}
    dates = [ensure_utc(datetime.fromisoformat(r["pub_date"])) for r in rows if r["pub_date"]]
    return known, (min(dates) if dates else None)


def _find_holes(src: RSSMediaSource, row: sqlite3.Row, known: Set[str], oldest) -> List[Episode]:
    """Feed entries missing from the DB whose pub_date is within tracked range."""
    missing = src.fetch_episodes(
        url=row["rss_url"],
        existing_episodes=[],
        last_processed=None,  # bypass incremental date filter
        max_episodes=None,  # bypass the cap
        known_external_ids=known,  # dedup against what we already have
        podcast_slug=row["slug"],
    )
    holes = []
    for ep in missing:
        d = ensure_utc(ep.pub_date) if ep.pub_date else None
        if d and oldest and d >= oldest:
            ep.podcast_id = row["id"]
            holes.append(ep)
    holes.sort(key=lambda e: ensure_utc(e.pub_date), reverse=True)
    return holes


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill feed holes left by the old refresh cap.")
    parser.add_argument("--apply", action="store_true", help="Write inserts (default: dry-run).")
    parser.add_argument("--podcast-id", help="Restrict to a single podcast id.")
    parser.add_argument("--db", default=DB, help=f"SQLite path (default: {DB}).")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    repo = SqlitePodcastRepository(args.db) if args.apply else None
    src = RSSMediaSource()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Scanning for feed holes in {args.db}\n")

    total_holes = 0
    per_pod: Dict[str, int] = {}
    skipped_youtube = 0

    for row in _load_podcasts(con, args.podcast_id):
        if _is_youtube(row["rss_url"]):
            skipped_youtube += 1
            continue

        try:
            known, oldest = _tracked(con, row["id"])
            if oldest is None:
                continue  # no tracked episodes => first-refresh territory, no hole
            holes = _find_holes(src, row, known, oldest)
        except Exception as exc:  # noqa: BLE001 - one bad feed must not abort the sweep
            print(f"  ! {row['title'][:50]:52} ERROR: {exc}")
            continue

        if not holes:
            continue

        per_pod[row["title"]] = len(holes)
        total_holes += len(holes)
        span = f"{ensure_utc(holes[-1].pub_date):%Y-%m-%d} .. {ensure_utc(holes[0].pub_date):%Y-%m-%d}"
        print(f"  {row['title'][:50]:52} {len(holes):>3} holes  ({span})")
        for ep in holes:
            print(f"        {ensure_utc(ep.pub_date):%Y-%m-%d}  {ep.title[:60]}")

        if args.apply and repo is not None:
            repo.save_episodes(holes)

    print(
        f"\n{'Inserted' if args.apply else 'Would insert'}: {total_holes} episode(s) "
        f"across {len(per_pod)} podcast(s)."
    )
    if skipped_youtube:
        print(f"Skipped {skipped_youtube} YouTube-backed podcast(s) (RSS-only repair).")
    if not args.apply and total_holes:
        print("\nRe-run with --apply to write these inserts.")

    con.close()


if __name__ == "__main__":
    main()
