"""Build a table of top podcasts in the world.

Pulls Apple Podcasts charts (overall + every Apple genre) until at least
TARGET_COUNT unique podcasts are collected, then enriches each with:

- Podcast name
- RSS URL              (mandatory; rows without RSS are dropped)
- Apple Podcast URL
- YouTube channel URL  (best-effort, via yt-dlp search heuristic)
- Category / Subcategory (Apple's primary genre + first subgenre)

Writes JSON + CSV to data/top_podcasts.{json,csv}.

Run with the project venv:
    ./venv/bin/python scripts/build_top_podcasts.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests
import yt_dlp

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CATEGORIES_FILE = DATA_DIR / "podcast_categories.json"

# Reuse the canonical name-normalization from the package so this script and the
# DB resolver match exactly. ROOT must be on sys.path before importing.
sys.path.insert(0, str(ROOT))
from thestill.utils.podcast_categories import normalize_category_name as normalize_name  # noqa: E402

TARGET_COUNT = 500
COLLECT_OVERFETCH = 540  # over-collect so we can trim to TARGET_COUNT after dropping RSS-less rows
REGION = "us"  # overridden in main() per --region
USER_AGENT = "thestill-top-podcasts/1.0 (+https://github.com/ssarunic/thestill)"

HTTP_TIMEOUT = 20
HTTP_RETRIES = 3
HTTP_BACKOFF = 1.5

YT_WORKERS = 12
LOOKUP_WORKERS = 16
YT_NAME_MATCH_THRESHOLD = 0.55


def http_get_json(url: str) -> dict[str, Any] | None:
    """GET a JSON URL with retry + backoff."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    delay = 1.0
    for attempt in range(HTTP_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay *= HTTP_BACKOFF
                continue
            return None
        except (requests.RequestException, ValueError):
            time.sleep(delay)
            delay *= HTTP_BACKOFF
    return None


def fetch_apple_chart(genre_id: int | None, limit: int = 200) -> list[dict[str, Any]]:
    """Fetch Apple's top podcast chart for an optional genre. Returns raw entries."""
    if genre_id is None:
        url = f"https://itunes.apple.com/{REGION}/rss/toppodcasts/limit={limit}/json"
    else:
        url = f"https://itunes.apple.com/{REGION}/rss/toppodcasts/" f"limit={limit}/genre={genre_id}/json"
    payload = http_get_json(url)
    if not payload:
        return []
    feed = payload.get("feed") or {}
    entries = feed.get("entry") or []
    return entries if isinstance(entries, list) else []


def parse_entry(entry: dict[str, Any]) -> tuple[str, str, str] | None:
    """Pull (track_id, name, artist) out of an Apple chart entry."""
    try:
        track_id = entry["id"]["attributes"]["im:id"]
        name = entry["im:name"]["label"]
        artist = entry.get("im:artist", {}).get("label", "")
        return track_id, name, artist
    except (KeyError, TypeError):
        return None


def lookup_podcast(track_id: str) -> dict[str, Any] | None:
    """iTunes Lookup API for a podcast by trackId. Returns the result dict or None."""
    url = f"https://itunes.apple.com/lookup?id={track_id}&entity=podcast"
    payload = http_get_json(url)
    if not payload:
        return None
    results = payload.get("results") or []
    if not results:
        return None
    return results[0]


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def find_youtube_channel(podcast_name: str, artist: str) -> str | None:
    """Best-effort: find an official YouTube channel for the podcast.

    Uses yt-dlp's ytsearch to grab top videos for the query, then picks the
    channel that appears the most across top results, sanity-checked by fuzzy
    matching the podcast/artist name against the channel display name, the
    @handle, and the video title. Returns a YouTube channel URL (preferring
    the human-friendly @handle form), or None if no confident match.
    """
    query = f"{podcast_name} podcast"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": "ytsearch5",
        "socket_timeout": 15,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception:
        return None
    if not info:
        return None
    entries = info.get("entries") or []

    candidates: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not entry:
            continue
        channel = entry.get("channel") or entry.get("uploader") or ""
        # Prefer the @handle URL — more human-friendly.
        channel_url = entry.get("uploader_url") or entry.get("channel_url") or ""
        handle = (entry.get("uploader_id") or "").lstrip("@")
        title = entry.get("title") or ""
        if not channel_url:
            continue
        # Score the channel against multiple targets and pick the best.
        targets = [channel, handle, title]
        names_to_match = [podcast_name]
        if artist:
            names_to_match.append(artist)
        score = 0.0
        for tgt in targets:
            for nm in names_to_match:
                if not tgt or not nm:
                    continue
                # Boost if podcast name is a substring of the title/channel.
                norm_tgt = normalize_name(tgt)
                norm_nm = normalize_name(nm)
                if norm_nm and norm_nm in norm_tgt:
                    score = max(score, 0.95)
                else:
                    score = max(score, name_similarity(tgt, nm))
        bucket = candidates.setdefault(
            channel_url,
            {"channel": channel, "score": 0.0, "count": 0},
        )
        bucket["count"] += 1
        bucket["score"] = max(bucket["score"], score)

    if not candidates:
        return None
    # Prefer channels that appear most often in the top results, then by score.
    best_url, best = max(
        candidates.items(),
        key=lambda kv: (kv[1]["count"], kv[1]["score"]),
    )
    if best["score"] < YT_NAME_MATCH_THRESHOLD and best["count"] < 3:
        return None
    return best_url


def collect_chart_entries(category_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull Apple's top podcast charts and merge into one ranked list.

    Strategy:
    1. Pull the overall top-200 chart — these are the highest-popularity
       podcasts globally, kept as-is.
    2. Then round-robin across every Apple category, taking one entry at a
       time off the top of each category's chart, skipping anything already
       seen, until we've accumulated TARGET_COUNT unique podcasts. This gives
       each category a fair share of the long tail rather than draining one
       category before moving to the next.

    Each returned dict has:
        track_id, name, artist, source_genre (str | None), source_rank (int)
    """
    seen: dict[str, dict[str, Any]] = {}

    # 1. Overall top chart.
    print("  fetching chart: OVERALL", flush=True)
    overall = fetch_apple_chart(None)
    for rank, raw in enumerate(overall, start=1):
        parsed = parse_entry(raw)
        if not parsed:
            continue
        track_id, name, artist = parsed
        if track_id in seen:
            continue
        seen[track_id] = {
            "track_id": track_id,
            "name": name,
            "artist": artist,
            "source_genre": None,
            "source_rank": rank,
        }
    print(f"      overall contributed {len(seen)} unique entries", flush=True)

    # 2. Pre-fetch every category chart so we can round-robin off them.
    category_charts: list[tuple[str, list[dict[str, Any]]]] = []
    for cat in category_defs:
        print(f"  fetching chart: {cat['name']}", flush=True)
        category_charts.append((cat["name"], fetch_apple_chart(cat["genre_id"])))

    # 3. Round-robin: one entry per category per pass.
    cursors = {name: 0 for name, _ in category_charts}
    while len(seen) < COLLECT_OVERFETCH:
        progressed = False
        for genre_name, entries in category_charts:
            if len(seen) >= COLLECT_OVERFETCH:
                break
            idx = cursors[genre_name]
            while idx < len(entries):
                parsed = parse_entry(entries[idx])
                idx += 1
                if not parsed:
                    continue
                track_id, name, artist = parsed
                if track_id in seen:
                    continue
                seen[track_id] = {
                    "track_id": track_id,
                    "name": name,
                    "artist": artist,
                    "source_genre": genre_name,
                    "source_rank": idx,
                }
                progressed = True
                break
            cursors[genre_name] = idx
        if not progressed:
            break  # every category chart exhausted

    return list(seen.values())


def enrich_with_lookup(items: list[dict[str, Any]], top_level_names: set[str]) -> list[dict[str, Any]]:
    """Run iTunes Lookup for every item in parallel; merge feedUrl/genre back.

    Apple returns ``primaryGenreName`` as the leaf subgenre (e.g. "Daily News")
    and ``genres`` containing both the leaf and its top-level parent
    (e.g. ["Daily News", "Podcasts", "News"]). We pick the entry that matches
    our known top-level category list as ``category`` and treat the primary
    genre as ``subcategory`` when it is not itself a top-level category.
    """
    enriched: list[dict[str, Any]] = []

    def worker(item: dict[str, Any]) -> dict[str, Any]:
        result = lookup_podcast(item["track_id"]) or {}
        feed_url = result.get("feedUrl") or ""
        apple_url = result.get("collectionViewUrl") or result.get("trackViewUrl") or ""
        primary = result.get("primaryGenreName") or ""
        genres = [g for g in (result.get("genres") or []) if g and g.lower() != "podcasts"]

        category = ""
        for g in genres:
            if g in top_level_names:
                category = g
                break
        if not category and primary in top_level_names:
            category = primary
        if not category:
            category = item.get("source_genre") or ""

        subcategory = ""
        if primary and primary != category:
            subcategory = primary
        else:
            for g in genres:
                if g != category:
                    subcategory = g
                    break

        return {
            **item,
            "rss_url": feed_url,
            "apple_url": apple_url,
            "category": category,
            "subcategory": subcategory,
        }

    with ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
        futures = [pool.submit(worker, it) for it in items]
        for i, fut in enumerate(as_completed(futures), start=1):
            enriched.append(fut.result())
            if i % 50 == 0:
                print(f"  lookup progress: {i}/{len(items)}", flush=True)
    return enriched


def enrich_with_youtube(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run yt-dlp YouTube search for every item in parallel."""
    out: list[dict[str, Any]] = []

    def worker(item: dict[str, Any]) -> dict[str, Any]:
        yt = find_youtube_channel(item["name"], item.get("artist", "")) or ""
        return {**item, "youtube_url": yt}

    with ThreadPoolExecutor(max_workers=YT_WORKERS) as pool:
        futures = [pool.submit(worker, it) for it in items]
        for i, fut in enumerate(as_completed(futures), start=1):
            out.append(fut.result())
            if i % 25 == 0:
                print(f"  youtube progress: {i}/{len(items)}", flush=True)
    return out


def main() -> int:
    global REGION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--region",
        default="us",
        help="Apple region code (e.g. us, gb, de, fr, au, ca, jp). Default: us.",
    )
    args = parser.parse_args()
    REGION = args.region.lower()
    out_json = DATA_DIR / f"top_podcasts_{REGION}.json"
    out_csv = DATA_DIR / f"top_podcasts_{REGION}.csv"

    if not CATEGORIES_FILE.exists():
        print(f"missing {CATEGORIES_FILE}", file=sys.stderr)
        return 1

    print(f"region: {REGION}", flush=True)
    cats = json.loads(CATEGORIES_FILE.read_text())["categories"]
    top_level_names = {c["name"] for c in cats}

    print("[1/4] collecting Apple top charts...", flush=True)
    raw_items = collect_chart_entries(cats)
    print(f"      collected {len(raw_items)} unique chart entries", flush=True)

    print("[2/4] iTunes Lookup for RSS + categories...", flush=True)
    enriched = enrich_with_lookup(raw_items, top_level_names)

    # Preserve the original collection order (overall top chart first, then
    # round-robin across all categories). enrich_with_lookup runs in a
    # ThreadPoolExecutor which scrambles order, so reindex back to the order
    # of raw_items.
    order = {it["track_id"]: i for i, it in enumerate(raw_items)}
    enriched = [e for e in enriched if e.get("rss_url")]
    enriched.sort(key=lambda e: order.get(e["track_id"], 10**9))
    enriched = enriched[:TARGET_COUNT]
    print(f"      kept {len(enriched)} with RSS feeds", flush=True)

    print("[3/4] YouTube channel search (best-effort)...", flush=True)
    enriched = enrich_with_youtube(enriched)

    print("[4/4] writing output files...", flush=True)
    out_json.write_text(json.dumps(enriched, indent=2, ensure_ascii=False))

    fields = [
        "rank",
        "name",
        "artist",
        "rss_url",
        "apple_url",
        "youtube_url",
        "category",
        "subcategory",
        "source_genre",
        "track_id",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(enriched, start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "name": row["name"],
                    "artist": row.get("artist", ""),
                    "rss_url": row.get("rss_url", ""),
                    "apple_url": row.get("apple_url", ""),
                    "youtube_url": row.get("youtube_url", ""),
                    "category": row.get("category", ""),
                    "subcategory": row.get("subcategory", ""),
                    "source_genre": row.get("source_genre", "") or "OVERALL",
                    "track_id": row["track_id"],
                }
            )

    with_yt = sum(1 for r in enriched if r.get("youtube_url"))
    with_apple = sum(1 for r in enriched if r.get("apple_url"))
    with_cat = sum(1 for r in enriched if r.get("category"))
    print(
        f"done. {len(enriched)} rows | RSS: {len(enriched)} | "
        f"Apple: {with_apple} | YouTube: {with_yt} | category: {with_cat}",
        flush=True,
    )
    print(f"wrote {out_json}", flush=True)
    print(f"wrote {out_csv}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
