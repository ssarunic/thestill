"""Build a table of top podcasts in the world.

Pulls Apple Podcasts charts (overall + every Apple genre) until at least
TARGET_COUNT unique podcasts are collected, then enriches each with:

- Podcast name
- RSS URL              (mandatory; rows without RSS are dropped)
- Apple Podcast URL
- YouTube channel URL  (best-effort, via yt-dlp search heuristic)
- Category / Subcategory (Apple's primary genre + first subgenre)
- Release cadence + duration (parsed from RSS feed):
  * episodes_per_month     — release frequency, windowed over last 90 days
                              (falls back to lifetime cadence if newer)
  * avg_episode_minutes    — mean episode length in the same window
  * audio_hours_per_month  = episodes_per_month × avg_minutes / 60
  * window_days            — actual window used (90 or shorter for new shows)
  * episodes_in_window     — sample size

Writes JSON + CSV to data/top_podcasts.{json,csv} and prints summary
aggregates (total audio hours/month, transcription cost estimate at
TRANSCRIBE_COST_PER_HOUR).

Run with the project venv:
    ./venv/bin/python scripts/build_top_podcasts.py
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import feedparser
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
RSS_WORKERS = 12
YT_NAME_MATCH_THRESHOLD = 0.55

# Cadence/duration window. 90 days is long enough to smooth weekly vs biweekly
# noise but short enough to reflect the show's *current* schedule (a podcast
# that went on hiatus 6 months ago shouldn't look active).
CADENCE_WINDOW_DAYS = 90
DAYS_PER_MONTH = 30.0  # the avg-month convention used for episodes_per_month

# Used for cost estimates printed at the end. Keep audio hours as the
# durable measure; this £/hr factor will drift as we gather real data.
TRANSCRIBE_COST_PER_HOUR_GBP = 0.15


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


def parse_itunes_duration(raw: Any) -> float | None:
    """Parse an itunes:duration value into seconds.

    Apple permits three forms:
      * ``HH:MM:SS`` or ``H:MM:SS``
      * ``MM:SS`` or ``M:SS``
      * a bare integer string of seconds (e.g. ``"3360"``)
    Returns None on anything else (None, empty, garbage).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:
            h, m, sec = nums
            return h * 3600 + m * 60 + sec
        if len(nums) == 2:
            m, sec = nums
            return m * 60 + sec
        if len(nums) == 1:
            return nums[0]
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_feed_stats(rss_url: str) -> dict[str, Any]:
    """Pull cadence + average duration from a podcast's RSS feed.

    Returns a dict with episodes_per_month, avg_episode_minutes,
    audio_hours_per_month, window_days, episodes_in_window. All fields are
    None when the feed can't be fetched or has no parseable episodes.
    """
    blank = {
        "episodes_per_month": None,
        "avg_episode_minutes": None,
        "audio_hours_per_month": None,
        "window_days": None,
        "episodes_in_window": 0,
    }
    try:
        resp = requests.get(
            rss_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, */*"},
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code != 200 or not resp.content:
            return blank
        # Pass bytes directly; feedparser handles encoding.
        feed = feedparser.parse(resp.content)
    except (requests.RequestException, ValueError):
        return blank
    entries = feed.entries or []
    if not entries:
        return blank

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CADENCE_WINDOW_DAYS)

    # Collect (pub_date, duration_seconds) per episode that has a valid date.
    episode_facts: list[tuple[datetime, float | None]] = []
    for entry in entries:
        pub_struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if not pub_struct:
            continue
        try:
            pub_dt = datetime(*pub_struct[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        duration = parse_itunes_duration(getattr(entry, "itunes_duration", None))
        episode_facts.append((pub_dt, duration))

    if not episode_facts:
        return blank

    # Window = last CADENCE_WINDOW_DAYS, OR the show's full life if newer.
    earliest_pub = min(p for p, _ in episode_facts)
    actual_window_days = min(CADENCE_WINDOW_DAYS, max(1.0, (now - earliest_pub).total_seconds() / 86400))
    window_cutoff = now - timedelta(days=actual_window_days)
    in_window = [(p, d) for p, d in episode_facts if p >= window_cutoff]

    # If a podcast hasn't released anything in the window, treat as inactive.
    # episodes_per_month = 0 with a flag (window_days reflects the recent
    # gap; consumer can decide to skip it from polling cost calcs).
    if not in_window:
        return {
            "episodes_per_month": 0.0,
            "avg_episode_minutes": None,
            "audio_hours_per_month": 0.0,
            "window_days": round(actual_window_days, 1),
            "episodes_in_window": 0,
        }

    eps_in_window = len(in_window)
    eps_per_month = round(eps_in_window * DAYS_PER_MONTH / actual_window_days, 2)

    durations = [d for _, d in in_window if d is not None and d > 0]
    if durations:
        avg_minutes = round(statistics.mean(durations) / 60.0, 2)
        audio_hours_per_month = round(eps_per_month * avg_minutes / 60.0, 2)
    else:
        avg_minutes = None
        audio_hours_per_month = None

    return {
        "episodes_per_month": eps_per_month,
        "avg_episode_minutes": avg_minutes,
        "audio_hours_per_month": audio_hours_per_month,
        "window_days": round(actual_window_days, 1),
        "episodes_in_window": eps_in_window,
    }


def enrich_with_feed_stats(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run fetch_feed_stats for every item in parallel."""
    out: list[dict[str, Any]] = []

    def worker(item: dict[str, Any]) -> dict[str, Any]:
        stats = fetch_feed_stats(item["rss_url"])
        return {**item, **stats}

    with ThreadPoolExecutor(max_workers=RSS_WORKERS) as pool:
        futures = [pool.submit(worker, it) for it in items]
        for i, fut in enumerate(as_completed(futures), start=1):
            out.append(fut.result())
            if i % 25 == 0:
                print(f"  rss progress: {i}/{len(items)}", flush=True)
    return out


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

    print("[3/5] YouTube channel search (best-effort)...", flush=True)
    enriched = enrich_with_youtube(enriched)

    print("[4/5] RSS feed cadence + duration...", flush=True)
    enriched = enrich_with_feed_stats(enriched)
    # enrich_with_feed_stats also runs in a ThreadPoolExecutor — restore order.
    enriched.sort(key=lambda e: order.get(e["track_id"], 10**9))

    print("[5/5] writing output files...", flush=True)
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
        "episodes_per_month",
        "avg_episode_minutes",
        "audio_hours_per_month",
        "window_days",
        "episodes_in_window",
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
                    "episodes_per_month": row.get("episodes_per_month"),
                    "avg_episode_minutes": row.get("avg_episode_minutes"),
                    "audio_hours_per_month": row.get("audio_hours_per_month"),
                    "window_days": row.get("window_days"),
                    "episodes_in_window": row.get("episodes_in_window"),
                    "track_id": row["track_id"],
                }
            )

    # ---- Coverage + cost summary ----
    with_yt = sum(1 for r in enriched if r.get("youtube_url"))
    with_apple = sum(1 for r in enriched if r.get("apple_url"))
    with_cat = sum(1 for r in enriched if r.get("category"))
    with_cadence = sum(1 for r in enriched if r.get("episodes_per_month") is not None)
    with_duration = sum(1 for r in enriched if r.get("avg_episode_minutes") is not None)

    audio_hours_values = [r["audio_hours_per_month"] for r in enriched if r.get("audio_hours_per_month") is not None]
    total_hours = sum(audio_hours_values) if audio_hours_values else 0.0
    avg_hours_per_podcast = total_hours / len(audio_hours_values) if audio_hours_values else 0.0
    median_hours_per_podcast = statistics.median(audio_hours_values) if audio_hours_values else 0.0

    eps_values = [r["episodes_per_month"] for r in enriched if r.get("episodes_per_month") is not None]
    total_eps_per_month = sum(eps_values) if eps_values else 0.0

    inactive = sum(1 for r in enriched if r.get("episodes_per_month") == 0)

    print(
        f"\ndone. {len(enriched)} rows | RSS: {len(enriched)} | Apple: {with_apple} | "
        f"YouTube: {with_yt} | category: {with_cat} | "
        f"cadence: {with_cadence} | duration: {with_duration}",
        flush=True,
    )
    print(f"\n=== Cadence & cost summary ({REGION.upper()}) ===", flush=True)
    print(f"  inactive podcasts (no episodes in {CADENCE_WINDOW_DAYS}d): {inactive}", flush=True)
    print(f"  total episodes/month across catalog:   {total_eps_per_month:>10.0f}", flush=True)
    print(f"  total audio hours/month:               {total_hours:>10.1f}", flush=True)
    print(f"  avg audio hours/month per podcast:     {avg_hours_per_podcast:>10.2f}", flush=True)
    print(f"  median audio hours/month per podcast:  {median_hours_per_podcast:>10.2f}", flush=True)
    est_total = total_hours * TRANSCRIBE_COST_PER_HOUR_GBP
    est_avg = avg_hours_per_podcast * TRANSCRIBE_COST_PER_HOUR_GBP
    print(
        f"  est. transcription cost @ £{TRANSCRIBE_COST_PER_HOUR_GBP:.2f}/hr:",
        flush=True,
    )
    print(f"    total/month:        £{est_total:>10.2f}", flush=True)
    print(f"    avg per podcast:    £{est_avg:>10.2f}", flush=True)

    # Top 10 by audio volume — useful for "which podcasts dominate cost"
    top_volume = sorted(
        (r for r in enriched if r.get("audio_hours_per_month") is not None),
        key=lambda r: r["audio_hours_per_month"],
        reverse=True,
    )[:10]
    if top_volume:
        print("\n  top 10 by audio hours/month:", flush=True)
        for r in top_volume:
            print(
                f"    {r['audio_hours_per_month']:>6.1f}h  "
                f"({r['episodes_per_month']:>4.1f} eps × {r['avg_episode_minutes']:>5.1f} min)  "
                f"{r['name'][:60]}",
                flush=True,
            )
    print(f"\nwrote {out_json}", flush=True)
    print(f"wrote {out_csv}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
