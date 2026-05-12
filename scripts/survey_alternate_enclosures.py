"""Survey top podcasts for Podcasting 2.0 <podcast:alternateEnclosure> adoption.

Pulls Apple's top-podcast charts (overall + each genre), resolves each to its
RSS feed via the iTunes Lookup API, then fetches every feed and scans for
<podcast:alternateEnclosure> tags in the Podcasting 2.0 namespace. Writes two
artifacts to data/:

  - alternate_enclosure_survey.json     -- one row per podcast
  - alternate_enclosure_survey_summary.txt -- aggregate counts

Read-only research script. No DB writes, no downloads, no package changes.

Run with the project venv (or system python3 if requests is available):
    ./venv/bin/python scripts/survey_alternate_enclosures.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CATEGORIES_FILE = DATA_DIR / "podcast_categories.json"
OUT_JSON = DATA_DIR / "alternate_enclosure_survey.json"
OUT_SUMMARY = DATA_DIR / "alternate_enclosure_survey_summary.txt"

TARGET_COUNT = 500
COLLECT_OVERFETCH = 540
REGION = "us"
USER_AGENT = "thestill-alt-enclosure-survey/1.0 (+https://github.com/ssarunic/thestill)"
HTTP_TIMEOUT = 20
HTTP_RETRIES = 3
HTTP_BACKOFF = 1.5

LOOKUP_WORKERS = 16
RSS_WORKERS = 16

PC_NS = "https://podcastindex.org/namespace/1.0"
ALT_ENC_TAG = f"{{{PC_NS}}}alternateEnclosure"
SOURCE_TAG = f"{{{PC_NS}}}source"

VIDEO_MIME_PREFIXES = ("video/",)
HLS_MIMES = {"application/x-mpegurl", "application/vnd.apple.mpegurl"}


def http_get_json(url: str) -> dict[str, Any] | None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    delay = 1.0
    for _ in range(HTTP_RETRIES):
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


def http_get_text(url: str) -> tuple[str | None, str | None]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"}
    delay = 1.0
    last_err: str | None = None
    for _ in range(HTTP_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                resp.encoding = resp.encoding or "utf-8"
                return resp.text, None
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"http_{resp.status_code}"
                time.sleep(delay)
                delay *= HTTP_BACKOFF
                continue
            return None, f"http_{resp.status_code}"
        except requests.RequestException as exc:
            last_err = type(exc).__name__
            time.sleep(delay)
            delay *= HTTP_BACKOFF
    return None, last_err or "unknown"


def fetch_apple_chart(genre_id: int | None, limit: int = 200) -> list[dict[str, Any]]:
    if genre_id is None:
        url = f"https://itunes.apple.com/{REGION}/rss/toppodcasts/limit={limit}/json"
    else:
        url = f"https://itunes.apple.com/{REGION}/rss/toppodcasts/limit={limit}/genre={genre_id}/json"
    payload = http_get_json(url)
    if not payload:
        return []
    feed = payload.get("feed") or {}
    entries = feed.get("entry") or []
    return entries if isinstance(entries, list) else []


def parse_entry(entry: dict[str, Any]) -> tuple[str, str, str] | None:
    try:
        track_id = entry["id"]["attributes"]["im:id"]
        name = entry["im:name"]["label"]
        artist = entry.get("im:artist", {}).get("label", "")
        return track_id, name, artist
    except (KeyError, TypeError):
        return None


def collect_chart_entries(category_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}

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

    category_charts: list[tuple[str, list[dict[str, Any]]]] = []
    for cat in category_defs:
        print(f"  fetching chart: {cat['name']}", flush=True)
        category_charts.append((cat["name"], fetch_apple_chart(cat["genre_id"])))

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
            break

    return list(seen.values())


def lookup_podcast(track_id: str) -> dict[str, Any] | None:
    url = f"https://itunes.apple.com/lookup?id={track_id}&entity=podcast"
    payload = http_get_json(url)
    if not payload:
        return None
    results = payload.get("results") or []
    return results[0] if results else None


def resolve_rss_urls(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []

    def worker(item: dict[str, Any]) -> dict[str, Any]:
        result = lookup_podcast(item["track_id"]) or {}
        return {
            **item,
            "rss_url": result.get("feedUrl") or "",
            "apple_url": result.get("collectionViewUrl") or result.get("trackViewUrl") or "",
            "primary_genre": result.get("primaryGenreName") or "",
        }

    with ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
        futures = [pool.submit(worker, it) for it in items]
        for i, fut in enumerate(as_completed(futures), start=1):
            enriched.append(fut.result())
            if i % 50 == 0:
                print(f"  lookup progress: {i}/{len(items)}", flush=True)
    return enriched


def scan_feed_for_alt_enclosures(rss_url: str) -> dict[str, Any]:
    rss_text, err = http_get_text(rss_url)
    if err is not None or not rss_text:
        return {"fetch_error": err or "empty_body", "episode_count": 0,
                "alt_enclosure_count": 0, "video_alt_count": 0,
                "mime_types": {}, "sample_urls": []}

    try:
        root = ET.fromstring(rss_text)
    except ET.ParseError as exc:
        return {"fetch_error": f"xml_parse: {exc.__class__.__name__}",
                "episode_count": 0, "alt_enclosure_count": 0,
                "video_alt_count": 0, "mime_types": {}, "sample_urls": []}

    items = root.findall(".//item")
    alt_count = 0
    video_count = 0
    mime_counter: Counter[str] = Counter()
    sample_urls: list[dict[str, Any]] = []
    episodes_with_alt = 0
    height_seen: list[int] = []
    has_default_video = False

    for item in items:
        item_alts = item.findall(ALT_ENC_TAG)
        if not item_alts:
            continue
        episodes_with_alt += 1
        for alt in item_alts:
            alt_count += 1
            mime = (alt.get("type") or "").lower()
            mime_counter[mime or "(missing)"] += 1
            is_video = mime.startswith(VIDEO_MIME_PREFIXES) or mime in HLS_MIMES
            if is_video:
                video_count += 1
                if (alt.get("default") or "").lower() == "true":
                    has_default_video = True
            height_attr = alt.get("height")
            if height_attr and height_attr.isdigit():
                height_seen.append(int(height_attr))

            if len(sample_urls) < 3 and is_video:
                src_urls = [s.get("uri") for s in alt.findall(SOURCE_TAG) if s.get("uri")]
                primary_url = src_urls[0] if src_urls else None
                sample_urls.append({
                    "mime_type": mime,
                    "height": height_attr,
                    "bitrate": alt.get("bitrate"),
                    "default": alt.get("default"),
                    "source_uri": primary_url,
                    "source_count": len(src_urls),
                })

    return {
        "fetch_error": None,
        "episode_count": len(items),
        "episodes_with_alt": episodes_with_alt,
        "alt_enclosure_count": alt_count,
        "video_alt_count": video_count,
        "has_default_video": has_default_video,
        "mime_types": dict(mime_counter),
        "max_height": max(height_seen) if height_seen else None,
        "sample_urls": sample_urls,
    }


def survey_feeds(podcasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def worker(item: dict[str, Any]) -> dict[str, Any]:
        if not item.get("rss_url"):
            return {**item, "fetch_error": "no_rss_url", "episode_count": 0,
                    "alt_enclosure_count": 0, "video_alt_count": 0,
                    "mime_types": {}, "sample_urls": []}
        scan = scan_feed_for_alt_enclosures(item["rss_url"])
        return {**item, **scan}

    with ThreadPoolExecutor(max_workers=RSS_WORKERS) as pool:
        futures = [pool.submit(worker, it) for it in podcasts]
        for i, fut in enumerate(as_completed(futures), start=1):
            results.append(fut.result())
            if i % 25 == 0:
                print(f"  feed scan progress: {i}/{len(podcasts)}", flush=True)
    return results


def write_summary(results: list[dict[str, Any]], path: Path) -> str:
    total = len(results)
    with_rss = [r for r in results if r.get("rss_url")]
    fetch_failed = [r for r in with_rss if r.get("fetch_error")]
    fetched_ok = [r for r in with_rss if not r.get("fetch_error")]
    with_any_alt = [r for r in fetched_ok if r.get("alt_enclosure_count", 0) > 0]
    with_video_alt = [r for r in fetched_ok if r.get("video_alt_count", 0) > 0]
    with_default_video = [r for r in fetched_ok if r.get("has_default_video")]

    mime_totals: Counter[str] = Counter()
    for r in fetched_ok:
        for mime, n in (r.get("mime_types") or {}).items():
            mime_totals[mime] += n

    cat_adoption: Counter[str] = Counter()
    cat_totals: Counter[str] = Counter()
    for r in fetched_ok:
        cat = r.get("source_genre") or r.get("primary_genre") or "(unknown)"
        cat_totals[cat] += 1
        if r.get("video_alt_count", 0) > 0:
            cat_adoption[cat] += 1

    lines = []
    lines.append(f"Total podcasts surveyed: {total}")
    lines.append(f"  with RSS URL:          {len(with_rss)}")
    lines.append(f"  fetched OK:            {len(fetched_ok)}")
    lines.append(f"  fetch failures:        {len(fetch_failed)}")
    lines.append("")
    lines.append("ALT-ENCLOSURE ADOPTION (of fetched OK):")
    lines.append(f"  any <podcast:alternateEnclosure>:  {len(with_any_alt)} "
                 f"({100*len(with_any_alt)/max(len(fetched_ok),1):.1f}%)")
    lines.append(f"  with video alt-enclosure:          {len(with_video_alt)} "
                 f"({100*len(with_video_alt)/max(len(fetched_ok),1):.1f}%)")
    lines.append(f"  with default=true video:           {len(with_default_video)}")
    lines.append("")
    lines.append("MIME TYPE DISTRIBUTION (across all alt-enclosure tags):")
    for mime, n in mime_totals.most_common(20):
        lines.append(f"  {n:6d}  {mime}")
    lines.append("")
    lines.append("CATEGORY ADOPTION (video alt-enclosures / podcasts in category):")
    for cat, adopted in cat_adoption.most_common(15):
        total_cat = cat_totals[cat]
        pct = 100 * adopted / total_cat if total_cat else 0
        lines.append(f"  {adopted:3d}/{total_cat:3d} ({pct:5.1f}%)  {cat}")
    lines.append("")
    lines.append("EXAMPLES — first 15 podcasts with video alt-enclosures:")
    for r in with_video_alt[:15]:
        sample = (r.get("sample_urls") or [{}])[0]
        lines.append(f"  - {r.get('name')!r} [{r.get('source_genre') or r.get('primary_genre')}]")
        lines.append(f"      rss:  {r.get('rss_url')}")
        lines.append(f"      mime: {sample.get('mime_type')}  height: {sample.get('height')}  "
                     f"default: {sample.get('default')}")
        if sample.get("source_uri"):
            lines.append(f"      src:  {sample['source_uri']}")
    lines.append("")
    if fetch_failed:
        err_counter: Counter[str] = Counter(r.get("fetch_error") or "?" for r in fetch_failed)
        lines.append("FETCH-ERROR DISTRIBUTION:")
        for err, n in err_counter.most_common(10):
            lines.append(f"  {n:4d}  {err}")

    text = "\n".join(lines) + "\n"
    path.write_text(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    global REGION
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--target", type=int, default=TARGET_COUNT)
    parser.add_argument("--limit-feeds", type=int, default=None,
                        help="Cap number of feeds to scan (for quick testing)")
    args = parser.parse_args()
    REGION = args.region

    if not CATEGORIES_FILE.exists():
        print(f"ERROR: {CATEGORIES_FILE} not found", file=sys.stderr)
        return 2
    categories = json.loads(CATEGORIES_FILE.read_text())["categories"]

    print(f"[1/3] Collecting Apple chart entries (target={args.target})...", flush=True)
    chart_items = collect_chart_entries(categories)
    print(f"      collected {len(chart_items)} unique chart entries", flush=True)

    print("[2/3] Resolving RSS URLs via iTunes Lookup...", flush=True)
    enriched = resolve_rss_urls(chart_items)
    with_rss = [e for e in enriched if e.get("rss_url")]
    without_rss = [e for e in enriched if not e.get("rss_url")]
    print(f"      {len(with_rss)} with RSS, {len(without_rss)} without (dropped)", flush=True)
    enriched = with_rss[:args.target]

    if args.limit_feeds:
        enriched = enriched[:args.limit_feeds]
        print(f"      capped to {len(enriched)} feeds for testing", flush=True)

    print(f"[3/3] Scanning {len(enriched)} feeds for <podcast:alternateEnclosure>...", flush=True)
    results = survey_feeds(enriched)

    results.sort(key=lambda r: (-(r.get("video_alt_count") or 0), -(r.get("alt_enclosure_count") or 0), r.get("name") or ""))

    OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT_JSON} ({len(results)} rows)", flush=True)

    summary = write_summary(results, OUT_SUMMARY)
    print(f"Wrote {OUT_SUMMARY}\n", flush=True)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
