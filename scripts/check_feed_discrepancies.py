# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""Report DB-vs-RSS episode discrepancies for refreshable podcasts.

For every refresh-eligible podcast (followed / manually-added, non-synthetic)
this fetches the live RSS feed, extracts each entry's external id the same way
the refresh pipeline does (``guid`` -> ``id`` -> ``str(pub_date)``), and reports
episodes present in the feed but missing from the database.

It separates two kinds of absence:

  * EXPECTED back-catalogue — feed episodes published BEFORE the oldest episode
    we track. Thestill never backfills a feed's full history, so these are not
    bugs and are only summarised, not listed.
  * IN-WINDOW GAPS — feed episodes published at/after our oldest tracked episode
    but missing from the DB. Once a feed is tracked, every episode from the
    oldest tracked one forward should be present, so a hole here is a genuine
    discrepancy (the discovery-watermark-poisoning signature).

Read-only: it never writes to the database.

    ./venv/bin/python scripts/check_feed_discrepancies.py
    ./venv/bin/python scripts/check_feed_discrepancies.py --limit 5
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import feedparser
from dotenv import load_dotenv

from thestill.logging import configure_structlog
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.config import load_config
from thestill.utils.url_guard import guarded_get  # SSRF-guarded fetch used by the app


def _entry_external_id(entry) -> str:
    """Mirror RSSMediaSource's external-id derivation."""
    return entry.get("guid", entry.get("id", entry.get("published", "")))


def _entry_pub(entry) -> Optional[datetime]:
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def _check_one(
    podcast, db_ids: set, db_oldest: Optional[datetime]
) -> Tuple[str, int, int, List[Tuple[str, str]], int, Optional[str]]:
    """Return (title, feed_count, db_count, in_window_gaps[(pub,title)], backlog_count, error)."""
    try:
        resp = guarded_get(str(podcast.rss_url))
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001 — one bad feed must not abort the sweep
        return podcast.title, 0, len(db_ids), [], 0, str(exc)

    in_window: List[Tuple[datetime, str]] = []
    backlog = 0
    feed_count = 0
    for entry in feed.entries:
        feed_count += 1
        ext_id = _entry_external_id(entry)
        if not ext_id or ext_id in db_ids:
            continue
        pub = _entry_pub(entry)
        # In-window (genuine gap) when we can't date it, or it's at/after our
        # oldest tracked episode. Older-than-oldest = expected back-catalogue.
        if db_oldest is not None and pub is not None and pub < db_oldest:
            backlog += 1
        else:
            in_window.append((pub or datetime.min.replace(tzinfo=timezone.utc), entry.get("title", "?")))

    in_window.sort(reverse=True)
    listed = [(p.strftime("%Y-%m-%d %H:%M") if p.year > 1 else "?", t) for p, t in in_window]
    return podcast.title, feed_count, len(db_ids), listed, backlog, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="Max missing episodes to list per feed.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent feed fetches.")
    args = parser.parse_args()

    load_dotenv()
    configure_structlog()
    config = load_config()
    repo = SqlitePodcastRepository(db_path=config.database_path)

    podcasts, known_ids_by_podcast = repo.get_podcasts_for_refresh()

    # Oldest tracked pub_date per podcast — the lower bound of the tracked
    # window. Feed episodes before this are expected back-catalogue.
    oldest_by_podcast: Dict[str, Optional[datetime]] = {}
    with repo._get_connection() as conn:  # read-only query
        for row in conn.execute("SELECT podcast_id, MIN(pub_date) AS oldest FROM episodes GROUP BY podcast_id"):
            oldest = datetime.fromisoformat(row["oldest"]) if row["oldest"] else None
            if oldest is not None and oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)  # legacy naive rows -> UTC
            oldest_by_podcast[row["podcast_id"]] = oldest

    print(f"🔎 Checking {len(podcasts)} refreshable podcast(s) against their live RSS feeds…\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_check_one, p, known_ids_by_podcast.get(p.id, set()), oldest_by_podcast.get(p.id)): p
            for p in podcasts
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: len(r[3]), reverse=True)

    total_gaps = 0
    feeds_with_gaps = 0
    total_backlog = 0
    errors = 0
    for title, feed_count, db_count, gaps, backlog, error in results:
        total_backlog += backlog
        if error:
            errors += 1
            print(f"  ⚠️  {title[:55]:55}  fetch error: {error}")
            continue
        if not gaps:
            continue
        feeds_with_gaps += 1
        total_gaps += len(gaps)
        bl = f", +{backlog} back-catalogue" if backlog else ""
        print(f"  ❗ {title[:55]:55}  feed={feed_count:4} db={db_count:3}  IN-WINDOW GAPS={len(gaps)}{bl}")
        for pub, ep_title in gaps[: args.limit]:
            print(f"       - {pub:16} | {ep_title[:72]}")
        if len(gaps) > args.limit:
            print(f"       … +{len(gaps) - args.limit} more")

    print("\n" + "=" * 60)
    print(
        f"Summary: {feeds_with_gaps} feed(s) with in-window gaps, {total_gaps} genuinely-missing "
        f"episode(s); {total_backlog} expected back-catalogue episode(s) ignored; "
        f"{errors} fetch error(s); {len(podcasts)} feeds checked."
    )
    if total_gaps == 0 and errors == 0:
        print("✅ No in-window discrepancies — every tracked feed is complete from its oldest episode forward.")


if __name__ == "__main__":
    main()
